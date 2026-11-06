"""Dependency-aware plan execution with optional review and revision.

Timeout honesty (unchanged from M5)
-----------------------------------
Current interfaces do **not** support cooperative cancellation. Marking a
task ``timed_out`` means a provider timeout was reported for that attempt.
It does **not** prove underlying LLM/tool work has stopped. A concurrency
slot is released only after the task pipeline callable returns.
``execute()`` returns only after every submitted worker has finished.

Review runs inside the same pipeline and therefore holds the concurrency
slot for produce → review → revise cycles.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from app.agent.agent import Agent
from app.agent.models import AgentRequest, AgentResult
from app.agent.runner import (
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    AgentRunner,
)
from app.core.exceptions import LLMTimeoutError
from app.orchestration.context import build_task_prompt
from app.orchestration.exceptions import (
    ExecutionConfigurationError,
    ReviewGenerationError,
)
from app.orchestration.execution import (
    ExecutionResult,
    ExecutionStatus,
    TaskResult,
    TaskStatus,
)
from app.orchestration.models import ExecutionPlan, PlanTask, SpecialistRole
from app.orchestration.registry import AgentRegistry
from app.orchestration.review import (
    AttemptKind,
    AttemptRecord,
    ReviewDecision,
    ReviewPolicy,
    ReviewSettings,
)
from app.orchestration.review_prompts import build_revision_prompt
from app.orchestration.reviewer import OutputReviewer
from app.orchestration.validation import validate_plan
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_MAX_TASKS = 12

SpecialistRunner = Callable[[Agent, str], AgentResult]
# Optional progress hook: (event_name, payload) — used by M11 job event streams.
ProgressHook = Callable[[str, dict[str, object]], None]


class PlanExecutor:
    """Run a validated plan with dependency-aware concurrent scheduling."""

    def __init__(
        self,
        registry: AgentRegistry,
        *,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        max_tasks: int = DEFAULT_MAX_TASKS,
        max_dependency_chars: int | None = None,
        tool_registry: ToolRegistry | None = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        tool_timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
        specialist_runner: SpecialistRunner | None = None,
        review_enabled: bool = False,
        review_roles: Iterable[SpecialistRole] | None = None,
        max_revisions: int = 1,
        max_review_repair_attempts: int = 1,
        reviewer: OutputReviewer | None = None,
        progress_hook: ProgressHook | None = None,
    ) -> None:
        if not isinstance(registry, AgentRegistry):
            raise ExecutionConfigurationError("registry must be an AgentRegistry.")
        if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int):
            raise ExecutionConfigurationError(
                "max_concurrency must be a positive integer."
            )
        if max_concurrency < 1:
            raise ExecutionConfigurationError(
                "max_concurrency must be a positive integer."
            )
        if isinstance(max_tasks, bool) or not isinstance(max_tasks, int):
            raise ExecutionConfigurationError("max_tasks must be a positive integer.")
        if max_tasks < 1:
            raise ExecutionConfigurationError("max_tasks must be a positive integer.")
        roles = frozenset(review_roles) if review_roles is not None else None
        self._review_settings = ReviewSettings(
            enabled=review_enabled,
            roles=roles,
            max_revisions=max_revisions,
            max_review_repair_attempts=max_review_repair_attempts,
        )
        if review_enabled and reviewer is None:
            raise ExecutionConfigurationError(
                "reviewer is required when review_enabled is true."
            )
        if reviewer is not None and not isinstance(reviewer, OutputReviewer):
            raise ExecutionConfigurationError("reviewer must be an OutputReviewer.")
        self._registry = registry
        self.max_concurrency = max_concurrency
        self.max_tasks = max_tasks
        self.max_dependency_chars = max_dependency_chars
        self._tool_registry = tool_registry
        self.max_iterations = max_iterations
        self.tool_timeout_seconds = tool_timeout_seconds
        self._specialist_runner = specialist_runner
        self._reviewer = reviewer
        self._progress_hook = progress_hook

    def _emit(self, event_name: str, **payload: object) -> None:
        if self._progress_hook is None:
            return
        try:
            self._progress_hook(event_name, payload)
        except Exception:
            logger.exception("progress_hook_failed event=%s", event_name)

    @property
    def review_settings(self) -> ReviewSettings:
        return self._review_settings

    def execute(
        self,
        plan: ExecutionPlan,
        *,
        memory_reference: str | None = None,
    ) -> ExecutionResult:
        """Execute ``plan`` and return structured per-task results.

        Downstream tasks only receive outputs from tasks with status
        ``succeeded`` (eligible for downstream use). Rejected drafts stay in
        attempt history and are never published as dependency inputs.

        ``memory_reference`` is optional untrusted prior-run context.
        """

        if not isinstance(plan, ExecutionPlan):
            raise ExecutionConfigurationError("plan must be an ExecutionPlan.")
        validate_plan(plan, max_tasks=self.max_tasks)
        roles = {task.role for task in plan.tasks}
        self._registry.require_roles(roles)

        tasks = {task.id: task for task in plan.tasks}
        status: dict[str, TaskStatus] = {
            task_id: TaskStatus.PENDING for task_id in tasks
        }
        results: dict[str, TaskResult] = {}
        outputs: dict[str, str] = {}
        started = time.perf_counter()
        logger.info(
            "execute_start goal_tasks=%s max_concurrency=%s review_enabled=%s "
            "max_revisions=%s",
            len(tasks),
            self.max_concurrency,
            self._review_settings.enabled,
            self._review_settings.max_revisions,
        )

        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            inflight: dict[Future[TaskResult], str] = {}

            def submit_ready() -> None:
                while len(inflight) < self.max_concurrency:
                    ready_id = _next_ready_task(tasks, status)
                    if ready_id is None:
                        return
                    task = tasks[ready_id]
                    status[ready_id] = TaskStatus.RUNNING
                    agent = self._registry.get(task.role)
                    dep_outputs = {dep: outputs[dep] for dep in task.depends_on}
                    prompt = build_task_prompt(
                        goal=plan.goal,
                        task=task,
                        dependency_outputs=dep_outputs,
                        max_dependency_chars=self.max_dependency_chars,
                        memory_reference=memory_reference,
                    )
                    logger.info(
                        "task_submit id=%s role=%s agent=%s review=%s",
                        task.id,
                        task.role.value,
                        agent.name,
                        self._review_settings.applies_to(task.role),
                    )
                    self._emit(
                        "task.started",
                        task_id=task.id,
                        role=task.role.value,
                        agent=agent.name,
                    )
                    future = pool.submit(
                        self._run_task_pipeline,
                        agent=agent,
                        task=task,
                        prompt=prompt,
                        goal=plan.goal,
                        dependency_outputs=dep_outputs,
                    )
                    inflight[future] = ready_id

            submit_ready()
            while inflight:
                done, _ = wait(tuple(inflight.keys()), return_when=FIRST_COMPLETED)
                for future in done:
                    task_id = inflight.pop(future)
                    task_result = future.result()
                    results[task_id] = task_result
                    status[task_id] = task_result.status
                    self._emit(
                        "task.completed",
                        task_id=task_id,
                        status=task_result.status.value,
                        quality_approved=task_result.quality_approved,
                        review_policy=task_result.review_policy.value,
                    )
                    if task_result.status is TaskStatus.SUCCEEDED:
                        outputs[task_id] = task_result.output or ""
                        logger.info(
                            "task_succeeded id=%s quality_approved=%s policy=%s",
                            task_id,
                            task_result.quality_approved,
                            task_result.review_policy.value,
                        )
                    else:
                        logger.info(
                            "task_unsuccessful id=%s status=%s error=%s",
                            task_id,
                            task_result.status.value,
                            task_result.error,
                        )
                        _skip_dependents(
                            failed_id=task_id,
                            tasks=tasks,
                            status=status,
                            results=results,
                            reason=(
                                f"dependency '{task_id}' is not eligible for "
                                f"downstream use ({task_result.status.value})."
                            ),
                        )
                submit_ready()
                if not inflight and _has_pending(status):
                    pending = [
                        task_id
                        for task_id, state in status.items()
                        if state is TaskStatus.PENDING
                    ]
                    raise ExecutionConfigurationError(
                        "Scheduler stalled with pending tasks and no runnable "
                        f"work: {', '.join(pending)}."
                    )

        duration_ms = (time.perf_counter() - started) * 1000
        task_results = [results[task.id] for task in plan.tasks]
        return _build_execution_result(
            goal=plan.goal,
            task_results=task_results,
            duration_ms=duration_ms,
            review_enabled=self._review_settings.enabled,
        )

    def _run_task_pipeline(
        self,
        *,
        agent: Agent,
        task: PlanTask,
        prompt: str,
        goal: str,
        dependency_outputs: dict[str, str],
    ) -> TaskResult:
        started = time.perf_counter()
        attempts: list[AttemptRecord] = []
        review_decision_count = 0
        reviewer_model_calls = 0
        reviewer_repair_calls = 0
        revision_count = 0

        should_review = self._review_settings.applies_to(task.role)
        if should_review:
            policy = ReviewPolicy.REVIEWED
            bypass_reason = None
        else:
            reason = self._review_settings.bypass_reason(task.role) or "review_disabled"
            if reason == "review_disabled":
                policy = ReviewPolicy.BYPASSED_REVIEW_DISABLED
            else:
                policy = ReviewPolicy.BYPASSED_ROLE_FILTER
            bypass_reason = reason

        current_prompt = prompt
        kind = AttemptKind.INITIAL
        while True:
            attempt_started = time.perf_counter()
            try:
                agent_result = self._run_specialist(agent, current_prompt)
            except LLMTimeoutError as exc:
                attempts.append(
                    AttemptRecord(
                        index=len(attempts),
                        kind=kind,
                        error=str(exc),
                        duration_ms=(time.perf_counter() - attempt_started) * 1000,
                    ).snapshot()
                )
                return TaskResult(
                    task_id=task.id,
                    role=task.role.value,
                    agent_name=agent.name,
                    status=TaskStatus.TIMED_OUT,
                    error=str(exc),
                    duration_ms=(time.perf_counter() - started) * 1000,
                    attempts=list(attempts),
                    review_policy=policy,
                    review_bypass_reason=bypass_reason,
                    quality_approved=False,
                    review_decision_count=review_decision_count,
                    reviewer_model_calls=reviewer_model_calls,
                    reviewer_repair_calls=reviewer_repair_calls,
                    revision_count=revision_count,
                )
            except Exception as exc:
                attempts.append(
                    AttemptRecord(
                        index=len(attempts),
                        kind=kind,
                        error=str(exc) or exc.__class__.__name__,
                        duration_ms=(time.perf_counter() - attempt_started) * 1000,
                    ).snapshot()
                )
                return TaskResult(
                    task_id=task.id,
                    role=task.role.value,
                    agent_name=agent.name,
                    status=TaskStatus.FAILED,
                    error=str(exc) or exc.__class__.__name__,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    attempts=list(attempts),
                    review_policy=policy,
                    review_bypass_reason=bypass_reason,
                    quality_approved=False,
                    review_decision_count=review_decision_count,
                    reviewer_model_calls=reviewer_model_calls,
                    reviewer_repair_calls=reviewer_repair_calls,
                    revision_count=revision_count,
                )

            attempt = AttemptRecord(
                index=len(attempts),
                kind=kind,
                output=agent_result.final_answer,
                duration_ms=(time.perf_counter() - attempt_started) * 1000,
                model=agent_result.model,
                usage=agent_result.usage,
            )

            if not should_review:
                attempts.append(attempt.snapshot())
                return TaskResult(
                    task_id=task.id,
                    role=task.role.value,
                    agent_name=agent_result.agent_name,
                    status=TaskStatus.SUCCEEDED,
                    output=agent_result.final_answer,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    model=agent_result.model,
                    usage=agent_result.usage,
                    attempts=list(attempts),
                    review_policy=policy,
                    review_bypass_reason=bypass_reason,
                    quality_approved=False,
                    review_decision_count=0,
                    reviewer_model_calls=0,
                    reviewer_repair_calls=0,
                    revision_count=0,
                )

            assert self._reviewer is not None
            try:
                review = self._reviewer.review(
                    goal=goal,
                    task=task,
                    specialist_output=agent_result.final_answer,
                    dependency_outputs=dependency_outputs,
                )
            except LLMTimeoutError as exc:
                attempts.append(attempt.snapshot())
                return TaskResult(
                    task_id=task.id,
                    role=task.role.value,
                    agent_name=agent_result.agent_name,
                    status=TaskStatus.TIMED_OUT,
                    output=agent_result.final_answer,
                    error=f"Reviewer timed out: {exc}",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    model=agent_result.model,
                    usage=agent_result.usage,
                    attempts=list(attempts),
                    review_policy=policy,
                    review_bypass_reason=None,
                    quality_approved=False,
                    review_decision_count=review_decision_count,
                    reviewer_model_calls=reviewer_model_calls,
                    reviewer_repair_calls=reviewer_repair_calls,
                    revision_count=revision_count,
                )
            except (ReviewGenerationError, Exception) as exc:
                attempts.append(attempt.snapshot())
                return TaskResult(
                    task_id=task.id,
                    role=task.role.value,
                    agent_name=agent_result.agent_name,
                    status=TaskStatus.FAILED,
                    output=agent_result.final_answer,
                    error=f"Reviewer error: {exc}",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    model=agent_result.model,
                    usage=agent_result.usage,
                    attempts=list(attempts),
                    review_policy=policy,
                    review_bypass_reason=None,
                    quality_approved=False,
                    review_decision_count=review_decision_count,
                    reviewer_model_calls=reviewer_model_calls,
                    reviewer_repair_calls=reviewer_repair_calls,
                    revision_count=revision_count,
                )

            review_decision_count += 1
            reviewer_model_calls += review.reviewer_model_calls
            reviewer_repair_calls += review.reviewer_repair_calls
            attempt = AttemptRecord(
                index=attempt.index,
                kind=attempt.kind,
                output=attempt.output,
                duration_ms=attempt.duration_ms,
                model=attempt.model,
                usage=attempt.usage,
                review=review.snapshot(),
            )
            attempts.append(attempt.snapshot())
            self._emit(
                "review.decision",
                task_id=task.id,
                decision=review.decision.value,
                attempt_index=attempt.index,
            )

            if review.decision is ReviewDecision.ACCEPTED:
                return TaskResult(
                    task_id=task.id,
                    role=task.role.value,
                    agent_name=agent_result.agent_name,
                    status=TaskStatus.SUCCEEDED,
                    output=agent_result.final_answer,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    model=agent_result.model,
                    usage=agent_result.usage,
                    attempts=list(attempts),
                    review_policy=policy,
                    review_bypass_reason=None,
                    quality_approved=True,
                    review_decision_count=review_decision_count,
                    reviewer_model_calls=reviewer_model_calls,
                    reviewer_repair_calls=reviewer_repair_calls,
                    revision_count=revision_count,
                )

            if review.decision is ReviewDecision.CANNOT_ASSESS:
                return TaskResult(
                    task_id=task.id,
                    role=task.role.value,
                    agent_name=agent_result.agent_name,
                    status=TaskStatus.FAILED,
                    output=agent_result.final_answer,
                    error=(
                        "Reviewer could not assess the output: "
                        f"{review.feedback}"
                    ),
                    duration_ms=(time.perf_counter() - started) * 1000,
                    model=agent_result.model,
                    usage=agent_result.usage,
                    attempts=list(attempts),
                    review_policy=policy,
                    review_bypass_reason=None,
                    quality_approved=False,
                    review_decision_count=review_decision_count,
                    reviewer_model_calls=reviewer_model_calls,
                    reviewer_repair_calls=reviewer_repair_calls,
                    revision_count=revision_count,
                )

            # needs_revision
            if revision_count >= self._review_settings.max_revisions:
                return TaskResult(
                    task_id=task.id,
                    role=task.role.value,
                    agent_name=agent_result.agent_name,
                    status=TaskStatus.REJECTED,
                    output=agent_result.final_answer,
                    error=(
                        "Revision limit exhausted while reviewer still required "
                        f"changes: {review.feedback}"
                    ),
                    duration_ms=(time.perf_counter() - started) * 1000,
                    model=agent_result.model,
                    usage=agent_result.usage,
                    attempts=list(attempts),
                    review_policy=policy,
                    review_bypass_reason=None,
                    quality_approved=False,
                    review_decision_count=review_decision_count,
                    reviewer_model_calls=reviewer_model_calls,
                    reviewer_repair_calls=reviewer_repair_calls,
                    revision_count=revision_count,
                )

            revision_count += 1
            kind = AttemptKind.REVISION
            self._emit(
                "revision.started",
                task_id=task.id,
                revision_count=revision_count,
            )
            current_prompt = build_revision_prompt(
                original_prompt=prompt,
                previous_output=agent_result.final_answer,
                review_feedback=review.feedback,
                issues=list(review.issues),
            )

    def _run_specialist(self, agent: Agent, prompt: str) -> AgentResult:
        if self._specialist_runner is not None:
            return self._specialist_runner(agent, prompt)
        registry = (
            self._tool_registry if self._tool_registry is not None else ToolRegistry()
        )
        runner = AgentRunner(
            registry,
            max_iterations=self.max_iterations,
            tool_timeout_seconds=self.tool_timeout_seconds,
        )
        return runner.run(agent, AgentRequest(task=prompt))


def _next_ready_task(
    tasks: dict[str, PlanTask],
    status: dict[str, TaskStatus],
) -> str | None:
    ready = [
        task_id
        for task_id, task in tasks.items()
        if status[task_id] is TaskStatus.PENDING
        and all(status[dep] is TaskStatus.SUCCEEDED for dep in task.depends_on)
    ]
    if not ready:
        return None
    ready.sort()
    return ready[0]


def _has_pending(status: dict[str, TaskStatus]) -> bool:
    return any(state is TaskStatus.PENDING for state in status.values())


def _skip_dependents(
    *,
    failed_id: str,
    tasks: dict[str, PlanTask],
    status: dict[str, TaskStatus],
    results: dict[str, TaskResult],
    reason: str,
) -> None:
    changed = True
    while changed:
        changed = False
        for task_id, task in tasks.items():
            if status[task_id] is not TaskStatus.PENDING:
                continue
            blocking = [
                dep
                for dep in task.depends_on
                if status[dep]
                in {
                    TaskStatus.FAILED,
                    TaskStatus.TIMED_OUT,
                    TaskStatus.SKIPPED,
                    TaskStatus.REJECTED,
                }
            ]
            if not blocking:
                continue
            status[task_id] = TaskStatus.SKIPPED
            results[task_id] = TaskResult(
                task_id=task_id,
                role=task.role.value,
                status=TaskStatus.SKIPPED,
                error=reason
                if failed_id in task.depends_on
                else "A dependency is not eligible for downstream use.",
                review_policy=ReviewPolicy.BYPASSED_REVIEW_DISABLED,
                review_bypass_reason="task_skipped",
                quality_approved=False,
            )
            changed = True
            logger.info("task_skipped id=%s blocked_by=%s", task_id, ",".join(blocking))


def _build_execution_result(
    *,
    goal: str,
    task_results: list[TaskResult],
    duration_ms: float,
    review_enabled: bool,
) -> ExecutionResult:
    succeeded = sum(1 for item in task_results if item.status is TaskStatus.SUCCEEDED)
    failed = sum(1 for item in task_results if item.status is TaskStatus.FAILED)
    timed_out = sum(1 for item in task_results if item.status is TaskStatus.TIMED_OUT)
    skipped = sum(1 for item in task_results if item.status is TaskStatus.SKIPPED)
    rejected = sum(1 for item in task_results if item.status is TaskStatus.REJECTED)
    overall = (
        ExecutionStatus.SUCCEEDED
        if failed == 0 and timed_out == 0 and skipped == 0 and rejected == 0
        else ExecutionStatus.FAILED
    )
    logger.info(
        "execute_done status=%s duration_ms=%.2f succeeded=%s failed=%s "
        "timed_out=%s skipped=%s rejected=%s",
        overall.value,
        duration_ms,
        succeeded,
        failed,
        timed_out,
        skipped,
        rejected,
    )
    return ExecutionResult(
        goal=goal,
        status=overall,
        task_results=task_results,
        duration_ms=duration_ms,
        succeeded_count=succeeded,
        failed_count=failed,
        timed_out_count=timed_out,
        skipped_count=skipped,
        rejected_count=rejected,
        review_decision_count=sum(item.review_decision_count for item in task_results),
        reviewer_model_calls=sum(item.reviewer_model_calls for item in task_results),
        reviewer_repair_calls=sum(item.reviewer_repair_calls for item in task_results),
        revision_count=sum(item.revision_count for item in task_results),
        review_enabled=review_enabled,
    )
