"""Deterministic validation tests for execution plans. No LLM calls."""

import pytest

from app.orchestration import (
    ExecutionPlan,
    PlanTask,
    PlanValidationError,
    SpecialistRole,
    execution_order,
    validate_plan,
)


def _task(
    task_id: str,
    *,
    depends_on: list[str] | None = None,
    role: SpecialistRole = SpecialistRole.GENERAL,
) -> PlanTask:
    return PlanTask(
        id=task_id,
        description=f"Do {task_id}",
        role=role,
        depends_on=depends_on or [],
        expected_output=f"Output of {task_id}",
    )


def test_validate_plan_accepts_a_valid_dag() -> None:
    plan = ExecutionPlan(
        goal="Compare vendors and summarize",
        tasks=[
            _task("research_a", role=SpecialistRole.RESEARCHER),
            _task("research_b", role=SpecialistRole.RESEARCHER),
            _task(
                "summarize",
                depends_on=["research_a", "research_b"],
                role=SpecialistRole.WRITER,
            ),
        ],
    )

    validate_plan(plan, max_tasks=12)
    assert execution_order(plan) == ["research_a", "research_b", "summarize"]


def test_validate_plan_rejects_duplicate_ids() -> None:
    plan = ExecutionPlan(
        goal="Goal",
        tasks=[_task("same"), _task("same")],
    )

    with pytest.raises(PlanValidationError, match="duplicate task id"):
        validate_plan(plan, max_tasks=12)


def test_validate_plan_rejects_missing_dependency() -> None:
    plan = ExecutionPlan(
        goal="Goal",
        tasks=[_task("write", depends_on=["missing_research"])],
    )

    with pytest.raises(PlanValidationError, match="unknown id"):
        validate_plan(plan, max_tasks=12)


def test_validate_plan_rejects_self_dependency() -> None:
    plan = ExecutionPlan(
        goal="Goal",
        tasks=[_task("loop", depends_on=["loop"])],
    )

    with pytest.raises(PlanValidationError, match="depends on itself"):
        validate_plan(plan, max_tasks=12)


def test_validate_plan_rejects_duplicate_dependency_entries() -> None:
    plan = ExecutionPlan(
        goal="Goal",
        tasks=[
            _task("research"),
            _task("write", depends_on=["research", "research"]),
        ],
    )

    with pytest.raises(PlanValidationError, match="more than once"):
        validate_plan(plan, max_tasks=12)


def test_validate_plan_rejects_a_cycle() -> None:
    plan = ExecutionPlan(
        goal="Goal",
        tasks=[
            _task("a", depends_on=["b"]),
            _task("b", depends_on=["a"]),
        ],
    )

    with pytest.raises(PlanValidationError, match="circular dependency"):
        validate_plan(plan, max_tasks=12)


def test_validate_plan_rejects_a_longer_cycle() -> None:
    plan = ExecutionPlan(
        goal="Goal",
        tasks=[
            _task("a", depends_on=["c"]),
            _task("b", depends_on=["a"]),
            _task("c", depends_on=["b"]),
        ],
    )

    with pytest.raises(PlanValidationError, match="circular dependency"):
        validate_plan(plan, max_tasks=12)


def test_validate_plan_rejects_too_many_tasks() -> None:
    plan = ExecutionPlan(
        goal="Goal",
        tasks=[_task("one"), _task("two"), _task("three")],
    )

    with pytest.raises(PlanValidationError, match="maximum allowed is 2"):
        validate_plan(plan, max_tasks=2)
