"""PlanExecutor + review/revision integration tests. Mocked only."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Sequence

import pytest

from app.agent import Agent
from app.agent.models import AgentResult
from app.core.messages import ChatMessage
from app.llm.base import LLMClient, LLMResponse
from app.orchestration import (
    AgentRegistry,
    AttemptKind,
    ExecutionPlan,
    ExecutionStatus,
    OutputReviewer,
    PlanExecutor,
    PlanTask,
    ReviewDecision,
    ReviewPolicy,
    SpecialistRole,
    TaskStatus,
)


class ScriptedLLMClient(LLMClient):
    def __init__(self, responses: Sequence[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        self.calls += 1
        if not self._responses:
            raise AssertionError("No scripted reviewer responses left.")
        return self._responses.pop(0)


def _agent(name: str) -> Agent:
    class UnusedClient(LLMClient):
        def generate(
            self,
            messages: Sequence[ChatMessage],
            tools: Sequence[object] | None = None,
        ) -> LLMResponse:
            raise AssertionError("specialist_runner should be used in these tests")

    return Agent(
        name=name,
        system_instruction=f"You are {name}.",
        llm_client=UnusedClient(),
        allowed_tools=(),
    )


def _task(
    task_id: str,
    role: SpecialistRole,
    *,
    depends_on: list[str] | None = None,
) -> PlanTask:
    return PlanTask(
        id=task_id,
        description=f"Do {task_id}",
        role=role,
        depends_on=depends_on or [],
        expected_output=f"Output of {task_id}",
    )


def _review_payload(decision: str, feedback: str = "Fix concrete gaps.") -> dict[str, object]:
    return {
        "decision": decision,
        "feedback": feedback,
        "issues": ["Missing expected section"] if decision != "accepted" else [],
        "task_coverage": "partial" if decision != "accepted" else "complete",
        "expected_output_fit": "weak" if decision != "accepted" else "strong",
        "internal_consistency": "ok",
        "evidence_support": "ok",
        "limitations": "No independent fact checks.",
        "checks_supplied_evidence_only": True,
        "independently_verified_facts": False,
    }


def _reviewer(*decisions: str) -> OutputReviewer:
    responses = [
        LLMResponse(content=json.dumps(_review_payload(decision)), model="fake-reviewer")
        for decision in decisions
    ]
    return OutputReviewer(ScriptedLLMClient(responses))


def test_review_disabled_success_is_not_quality_approved() -> None:
    registry = AgentRegistry()
    registry.register(SpecialistRole.GENERAL, _agent("general"))

    def runner(agent: Agent, prompt: str) -> AgentResult:
        return AgentResult(final_answer="done", agent_name=agent.name, model="fake")

    result = PlanExecutor(registry, specialist_runner=runner).execute(
        ExecutionPlan(goal="Goal", tasks=[_task("only", SpecialistRole.GENERAL)])
    )
    item = result.task_results[0]
    assert item.status is TaskStatus.SUCCEEDED
    assert item.quality_approved is False
    assert item.review_policy is ReviewPolicy.BYPASSED_REVIEW_DISABLED
    assert item.review_bypass_reason == "review_disabled"
    assert item.reviewer_model_calls == 0
    assert result.review_enabled is False


def test_accept_on_first_review() -> None:
    registry = AgentRegistry()
    registry.register(SpecialistRole.WRITER, _agent("writer"))
    specialist_calls = 0

    def runner(agent: Agent, prompt: str) -> AgentResult:
        nonlocal specialist_calls
        specialist_calls += 1
        return AgentResult(final_answer="good draft", agent_name=agent.name, model="fake")

    result = PlanExecutor(
        registry,
        specialist_runner=runner,
        review_enabled=True,
        reviewer=_reviewer("accepted"),
        max_revisions=1,
    ).execute(ExecutionPlan(goal="Goal", tasks=[_task("write", SpecialistRole.WRITER)]))

    item = result.task_results[0]
    assert specialist_calls == 1
    assert item.status is TaskStatus.SUCCEEDED
    assert item.quality_approved is True
    assert item.review_policy is ReviewPolicy.REVIEWED
    assert item.review_decision_count == 1
    assert item.reviewer_model_calls == 1
    assert item.revision_count == 0
    assert item.attempts[0].review is not None
    assert item.attempts[0].review.decision is ReviewDecision.ACCEPTED
    assert item.attempts[0].review.independently_verified_facts is False


def test_revision_then_acceptance() -> None:
    registry = AgentRegistry()
    registry.register(SpecialistRole.WRITER, _agent("writer"))
    prompts: list[str] = []

    def runner(agent: Agent, prompt: str) -> AgentResult:
        prompts.append(prompt)
        text = "revised draft" if len(prompts) > 1 else "weak draft"
        return AgentResult(final_answer=text, agent_name=agent.name, model="fake")

    result = PlanExecutor(
        registry,
        specialist_runner=runner,
        review_enabled=True,
        reviewer=_reviewer("needs_revision", "accepted"),
        max_revisions=1,
    ).execute(ExecutionPlan(goal="Goal", tasks=[_task("write", SpecialistRole.WRITER)]))

    item = result.task_results[0]
    assert item.status is TaskStatus.SUCCEEDED
    assert item.quality_approved is True
    assert item.revision_count == 1
    assert item.review_decision_count == 2
    assert item.reviewer_model_calls == 2
    assert [a.kind for a in item.attempts] == [AttemptKind.INITIAL, AttemptKind.REVISION]
    assert "Reviewer feedback:" in prompts[1]
    assert "weak draft" in prompts[1]
    # History is append-only: first attempt still has the weak draft.
    assert item.attempts[0].output == "weak draft"
    assert item.output == "revised draft"


def test_revision_limit_exhausted_rejects_and_skips_downstream() -> None:
    registry = AgentRegistry()
    registry.register(SpecialistRole.WRITER, _agent("writer"))
    registry.register(SpecialistRole.REVIEWER, _agent("reviewer-role"))
    write_started = threading.Event()
    downstream_started = threading.Event()

    def runner(agent: Agent, prompt: str) -> AgentResult:
        if agent.name == "writer":
            write_started.set()
            return AgentResult(final_answer="still weak", agent_name=agent.name, model="fake")
        downstream_started.set()
        return AgentResult(final_answer="should not run", agent_name=agent.name, model="fake")

    result = PlanExecutor(
        registry,
        specialist_runner=runner,
        review_enabled=True,
        reviewer=_reviewer("needs_revision", "needs_revision"),
        max_revisions=1,
    ).execute(
        ExecutionPlan(
            goal="Goal",
            tasks=[
                _task("write", SpecialistRole.WRITER),
                _task("polish", SpecialistRole.REVIEWER, depends_on=["write"]),
            ],
        )
    )

    by_id = {item.task_id: item for item in result.task_results}
    assert write_started.is_set()
    assert not downstream_started.is_set()
    assert by_id["write"].status is TaskStatus.REJECTED
    assert by_id["write"].quality_approved is False
    assert by_id["write"].output == "still weak"
    assert len(by_id["write"].attempts) == 2
    assert by_id["polish"].status is TaskStatus.SKIPPED
    assert result.status is ExecutionStatus.FAILED
    assert result.rejected_count == 1


def test_cannot_assess_is_failed_with_reason() -> None:
    registry = AgentRegistry()
    registry.register(SpecialistRole.GENERAL, _agent("general"))

    def runner(agent: Agent, prompt: str) -> AgentResult:
        return AgentResult(final_answer="???", agent_name=agent.name, model="fake")

    result = PlanExecutor(
        registry,
        specialist_runner=runner,
        review_enabled=True,
        reviewer=_reviewer("cannot_assess"),
        max_revisions=1,
    ).execute(ExecutionPlan(goal="Goal", tasks=[_task("only", SpecialistRole.GENERAL)]))

    item = result.task_results[0]
    assert item.status is TaskStatus.FAILED
    assert "could not assess" in (item.error or "").lower()
    assert item.quality_approved is False


def test_downstream_waits_until_acceptance() -> None:
    registry = AgentRegistry()
    registry.register(SpecialistRole.RESEARCHER, _agent("researcher"))
    registry.register(SpecialistRole.WRITER, _agent("writer"))
    research_released = threading.Event()
    order: list[str] = []
    lock = threading.Lock()
    specialist_phase = {"research": 0}

    def runner(agent: Agent, prompt: str) -> AgentResult:
        if agent.name == "researcher":
            specialist_phase["research"] += 1
            if specialist_phase["research"] == 1:
                return AgentResult(
                    final_answer="incomplete notes",
                    agent_name=agent.name,
                    model="fake",
                )
            with lock:
                order.append("research_accepted_attempt")
            research_released.set()
            return AgentResult(
                final_answer="complete notes",
                agent_name=agent.name,
                model="fake",
            )
        if not research_released.wait(timeout=1.0):
            raise AssertionError("writer started before research was accepted")
        with lock:
            order.append("write")
        assert "complete notes" in prompt
        assert "incomplete notes" not in prompt
        return AgentResult(final_answer="summary", agent_name=agent.name, model="fake")

    result = PlanExecutor(
        registry,
        specialist_runner=runner,
        review_enabled=True,
        review_roles=[SpecialistRole.RESEARCHER],
        reviewer=_reviewer("needs_revision", "accepted"),
        max_revisions=1,
    ).execute(
        ExecutionPlan(
            goal="Research then write",
            tasks=[
                _task("research", SpecialistRole.RESEARCHER),
                _task("write", SpecialistRole.WRITER, depends_on=["research"]),
            ],
        )
    )

    by_id = {item.task_id: item for item in result.task_results}
    assert by_id["research"].quality_approved is True
    assert by_id["write"].status is TaskStatus.SUCCEEDED
    assert by_id["write"].quality_approved is False
    assert by_id["write"].review_policy is ReviewPolicy.BYPASSED_ROLE_FILTER
    assert order == ["research_accepted_attempt", "write"]


def test_role_filter_bypasses_selected_roles() -> None:
    registry = AgentRegistry()
    registry.register(SpecialistRole.GENERAL, _agent("general"))
    reviewer = _reviewer("accepted")

    def runner(agent: Agent, prompt: str) -> AgentResult:
        return AgentResult(final_answer="ok", agent_name=agent.name, model="fake")

    result = PlanExecutor(
        registry,
        specialist_runner=runner,
        review_enabled=True,
        review_roles=[SpecialistRole.WRITER],
        reviewer=reviewer,
    ).execute(ExecutionPlan(goal="Goal", tasks=[_task("only", SpecialistRole.GENERAL)]))

    item = result.task_results[0]
    assert item.review_policy is ReviewPolicy.BYPASSED_ROLE_FILTER
    assert item.quality_approved is False
    assert item.reviewer_model_calls == 0


def test_history_snapshots_are_frozen() -> None:
    registry = AgentRegistry()
    registry.register(SpecialistRole.WRITER, _agent("writer"))

    def runner(agent: Agent, prompt: str) -> AgentResult:
        return AgentResult(final_answer="draft", agent_name=agent.name, model="fake")

    result = PlanExecutor(
        registry,
        specialist_runner=runner,
        review_enabled=True,
        reviewer=_reviewer("accepted"),
    ).execute(ExecutionPlan(goal="Goal", tasks=[_task("write", SpecialistRole.WRITER)]))

    item = result.task_results[0]
    review = item.attempts[0].review
    assert review is not None
    with pytest.raises(Exception):
        review.feedback = "mutated later"  # type: ignore[misc]
    assert item.attempts[0].review is not None
    assert item.attempts[0].review.feedback != "mutated later"


def test_fake_worker_review_off_vs_review_on_timings_are_labeled() -> None:
    """Fake-worker wall times only. Not live API latency or quality proof."""

    registry = AgentRegistry()
    registry.register(SpecialistRole.GENERAL, _agent("general"))

    def runner(agent: Agent, prompt: str) -> AgentResult:
        time.sleep(0.05)
        return AgentResult(final_answer="ok", agent_name=agent.name, model="fake")

    class SlowReviewClient(LLMClient):
        def generate(
            self,
            messages: Sequence[ChatMessage],
            tools: Sequence[object] | None = None,
        ) -> LLMResponse:
            time.sleep(0.05)
            return LLMResponse(
                content=json.dumps(_review_payload("accepted")),
                model="fake-reviewer",
            )

    plan = ExecutionPlan(goal="Goal", tasks=[_task("only", SpecialistRole.GENERAL)])
    off = PlanExecutor(registry, specialist_runner=runner).execute(plan)
    on = PlanExecutor(
        registry,
        specialist_runner=runner,
        review_enabled=True,
        reviewer=OutputReviewer(SlowReviewClient()),
    ).execute(plan)

    assert off.review_enabled is False
    assert on.review_enabled is True
    assert on.reviewer_model_calls == 1
    assert on.duration_ms > off.duration_ms
    assert off.task_results[0].quality_approved is False
    assert on.task_results[0].quality_approved is True
