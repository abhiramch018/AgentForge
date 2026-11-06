"""Deterministic checks for execution plans.

These rules catch structural mistakes. They do not judge whether a plan is
a good decomposition of the user's goal — that still needs human or
model-based evaluation.
"""

from app.orchestration.exceptions import PlanValidationError
from app.orchestration.models import ExecutionPlan, PlanTask


def validate_plan(plan: ExecutionPlan, *, max_tasks: int) -> None:
    """Raise ``PlanValidationError`` when ``plan`` is structurally invalid."""

    if max_tasks < 1:
        raise PlanValidationError("max_tasks must be a positive integer.")
    if len(plan.tasks) > max_tasks:
        raise PlanValidationError(
            f"plan has {len(plan.tasks)} tasks; maximum allowed is {max_tasks}."
        )

    ids = [task.id for task in plan.tasks]
    seen: set[str] = set()
    duplicates: list[str] = []
    for task_id in ids:
        if task_id in seen and task_id not in duplicates:
            duplicates.append(task_id)
        seen.add(task_id)
    if duplicates:
        joined = ", ".join(duplicates)
        raise PlanValidationError(f"duplicate task id(s): {joined}.")

    id_set = set(ids)
    for task in plan.tasks:
        _validate_task_dependencies(task, id_set)

    cycle = _find_cycle(plan.tasks)
    if cycle is not None:
        raise PlanValidationError(
            "circular dependency detected: " + " -> ".join(cycle) + "."
        )


def execution_order(plan: ExecutionPlan) -> list[str]:
    """Return a topological order of task ids.

    Independent tasks may appear in any relative order. Callers in later
    milestones can run tasks with empty remaining dependencies concurrently.
    """

    validate_plan(plan, max_tasks=max(len(plan.tasks), 1))
    indegree = {task.id: 0 for task in plan.tasks}
    dependents: dict[str, list[str]] = {task.id: [] for task in plan.tasks}
    for task in plan.tasks:
        for dependency in task.depends_on:
            indegree[task.id] += 1
            dependents[dependency].append(task.id)

    ready = sorted(task_id for task_id, count in indegree.items() if count == 0)
    ordered: list[str] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for child in dependents[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    if len(ordered) != len(plan.tasks):
        raise PlanValidationError("circular dependency detected.")
    return ordered


def _validate_task_dependencies(task: PlanTask, known_ids: set[str]) -> None:
    seen_deps: set[str] = set()
    for dependency in task.depends_on:
        if dependency == task.id:
            raise PlanValidationError(
                f"task '{task.id}' depends on itself."
            )
        if dependency not in known_ids:
            raise PlanValidationError(
                f"task '{task.id}' depends on unknown id '{dependency}'."
            )
        if dependency in seen_deps:
            raise PlanValidationError(
                f"task '{task.id}' lists dependency '{dependency}' more than once."
            )
        seen_deps.add(dependency)


def _find_cycle(tasks: list[PlanTask]) -> list[str] | None:
    graph = {task.id: list(task.depends_on) for task in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def dfs(node: str) -> list[str] | None:
        visiting.add(node)
        stack.append(node)
        for dependency in graph[node]:
            if dependency in visiting:
                start = stack.index(dependency)
                return stack[start:] + [dependency]
            if dependency not in visited:
                found = dfs(dependency)
                if found is not None:
                    return found
        stack.pop()
        visiting.remove(node)
        visited.add(node)
        return None

    for task_id in graph:
        if task_id not in visited:
            found = dfs(task_id)
            if found is not None:
                return found
    return None
