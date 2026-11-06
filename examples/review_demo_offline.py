"""Deterministic offline demo of review and revision behavior.

Uses fake specialists and a scripted reviewer. No API key and no paid calls.
This demonstrates workflow and metrics, not live model quality.

Scenarios:
1. Acceptance on the first review
2. Revision followed by acceptance
3. Rejection after the revision limit
4. Downstream waiting for acceptance

Also prints fake-worker timings for review-off vs review-on.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence

from app.agent import Agent
from app.agent.models import AgentResult
from app.core.messages import ChatMessage
from app.llm.base import LLMClient, LLMResponse
from app.orchestration import (
    AgentRegistry,
    ExecutionPlan,
    OutputReviewer,
    PlanExecutor,
    PlanTask,
    SpecialistRole,
)


class ScriptedReviewClient(LLMClient):
    def __init__(self, decisions: Sequence[str]) -> None:
        self._decisions = list(decisions)
        self.model_calls = 0

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        self.model_calls += 1
        decision = self._decisions.pop(0)
        payload = {
            "decision": decision,
            "feedback": (
                "Add the missing concrete recommendation."
                if decision == "needs_revision"
                else (
                    "Too ambiguous to judge."
                    if decision == "cannot_assess"
                    else "Meets the assignment."
                )
            ),
            "issues": (
                ["Missing recommendation"] if decision == "needs_revision" else []
            ),
            "task_coverage": "checked",
            "expected_output_fit": "checked",
            "internal_consistency": "checked",
            "evidence_support": "checked against supplied text only",
            "limitations": "No independent fact verification.",
            "checks_supplied_evidence_only": True,
            "independently_verified_facts": False,
        }
        return LLMResponse(content=json.dumps(payload), model="scripted-reviewer")


def _agent(name: str) -> Agent:
    class Boom(LLMClient):
        def generate(
            self,
            messages: Sequence[ChatMessage],
            tools: Sequence[object] | None = None,
        ) -> LLMResponse:
            raise AssertionError("offline demo uses specialist_runner")

    return Agent(
        name=name,
        system_instruction=f"You are {name}.",
        llm_client=Boom(),
        allowed_tools=(),
    )


def _print_task(label: str, result) -> None:  # noqa: ANN001
    print(f"\n=== {label} ===")
    print(f"status={result.status.value} review_enabled={result.review_enabled}")
    print(
        f"metrics specialist_revisions={result.revision_count} "
        f"review_decisions={result.review_decision_count} "
        f"reviewer_model_calls={result.reviewer_model_calls} "
        f"reviewer_repair_calls={result.reviewer_repair_calls} "
        f"duration_ms={result.duration_ms:.2f} (FAKE WORKERS)"
    )
    for item in result.task_results:
        print(
            f"- {item.task_id}: status={item.status.value} "
            f"quality_approved={item.quality_approved} "
            f"policy={item.review_policy.value} "
            f"bypass={item.review_bypass_reason}"
        )
        for attempt in item.attempts:
            decision = attempt.review.decision.value if attempt.review else "-"
            print(
                f"  attempt[{attempt.index}] kind={attempt.kind.value} "
                f"output={attempt.output!r} review={decision}"
            )


def main() -> None:
    registry = AgentRegistry()
    registry.register(SpecialistRole.WRITER, _agent("writer"))
    registry.register(SpecialistRole.RESEARCHER, _agent("researcher"))

    # 1) Accept on first review
    def accept_runner(agent: Agent, prompt: str) -> AgentResult:
        return AgentResult(final_answer="Clear paragraph.", agent_name=agent.name, model="fake")

    accept = PlanExecutor(
        registry,
        specialist_runner=accept_runner,
        review_enabled=True,
        reviewer=OutputReviewer(ScriptedReviewClient(["accepted"])),
    ).execute(
        ExecutionPlan(
            goal="Write a short summary",
            tasks=[
                PlanTask(
                    id="write",
                    description="Write a short summary",
                    role=SpecialistRole.WRITER,
                    expected_output="One paragraph",
                )
            ],
        )
    )
    _print_task("1) Acceptance on first review", accept)

    # 2) Revision then acceptance
    drafts = {"n": 0}

    def revise_runner(agent: Agent, prompt: str) -> AgentResult:
        drafts["n"] += 1
        text = "Weak draft." if drafts["n"] == 1 else "Improved draft with recommendation."
        return AgentResult(final_answer=text, agent_name=agent.name, model="fake")

    revised = PlanExecutor(
        registry,
        specialist_runner=revise_runner,
        review_enabled=True,
        max_revisions=1,
        reviewer=OutputReviewer(
            ScriptedReviewClient(["needs_revision", "accepted"])
        ),
    ).execute(
        ExecutionPlan(
            goal="Write a short summary",
            tasks=[
                PlanTask(
                    id="write",
                    description="Write a short summary",
                    role=SpecialistRole.WRITER,
                    expected_output="One paragraph with a recommendation",
                )
            ],
        )
    )
    _print_task("2) Revision then acceptance", revised)

    # 3) Rejection after revision limit
    def weak_runner(agent: Agent, prompt: str) -> AgentResult:
        return AgentResult(final_answer="Still weak.", agent_name=agent.name, model="fake")

    rejected = PlanExecutor(
        registry,
        specialist_runner=weak_runner,
        review_enabled=True,
        max_revisions=1,
        reviewer=OutputReviewer(
            ScriptedReviewClient(["needs_revision", "needs_revision"])
        ),
    ).execute(
        ExecutionPlan(
            goal="Write a short summary",
            tasks=[
                PlanTask(
                    id="write",
                    description="Write a short summary",
                    role=SpecialistRole.WRITER,
                    expected_output="One paragraph with a recommendation",
                ),
                PlanTask(
                    id="followup",
                    description="Use the summary",
                    role=SpecialistRole.RESEARCHER,
                    depends_on=["write"],
                    expected_output="Note that depends on accepted summary",
                ),
            ],
        )
    )
    _print_task("3) Rejection after revision limit (+ downstream skipped)", rejected)

    # 4) Downstream waits for acceptance
    phase = {"research": 0}

    def gated_runner(agent: Agent, prompt: str) -> AgentResult:
        if agent.name == "researcher":
            phase["research"] += 1
            if phase["research"] == 1:
                return AgentResult(
                    final_answer="Incomplete notes",
                    agent_name=agent.name,
                    model="fake",
                )
            return AgentResult(
                final_answer="Complete notes",
                agent_name=agent.name,
                model="fake",
            )
        assert "Complete notes" in prompt
        assert "Incomplete notes" not in prompt
        return AgentResult(final_answer="Downstream used accepted notes.", agent_name=agent.name, model="fake")

    gated = PlanExecutor(
        registry,
        specialist_runner=gated_runner,
        review_enabled=True,
        review_roles=[SpecialistRole.RESEARCHER],
        max_revisions=1,
        reviewer=OutputReviewer(
            ScriptedReviewClient(["needs_revision", "accepted"])
        ),
    ).execute(
        ExecutionPlan(
            goal="Research then write",
            tasks=[
                PlanTask(
                    id="research",
                    description="Gather notes",
                    role=SpecialistRole.RESEARCHER,
                    expected_output="Complete notes",
                ),
                PlanTask(
                    id="write",
                    description="Write from notes",
                    role=SpecialistRole.WRITER,
                    depends_on=["research"],
                    expected_output="Short write-up",
                ),
            ],
        )
    )
    _print_task("4) Downstream waits for acceptance", gated)

    # Fake-worker timing comparison (labeled)
    def sleepy(agent: Agent, prompt: str) -> AgentResult:
        time.sleep(0.05)
        return AgentResult(final_answer="ok", agent_name=agent.name, model="fake")

    class SlowReview(LLMClient):
        def generate(
            self,
            messages: Sequence[ChatMessage],
            tools: Sequence[object] | None = None,
        ) -> LLMResponse:
            time.sleep(0.05)
            return LLMResponse(
                content=json.dumps(
                    {
                        "decision": "accepted",
                        "feedback": "ok",
                        "issues": [],
                        "task_coverage": "ok",
                        "expected_output_fit": "ok",
                        "internal_consistency": "ok",
                        "evidence_support": "ok",
                        "limitations": "none beyond no fact check",
                        "checks_supplied_evidence_only": True,
                        "independently_verified_facts": False,
                    }
                ),
                model="scripted-reviewer",
            )

    plan = ExecutionPlan(
        goal="Timing demo",
        tasks=[
            PlanTask(
                id="only",
                description="Do work",
                role=SpecialistRole.WRITER,
                expected_output="Done",
            )
        ],
    )
    off = PlanExecutor(registry, specialist_runner=sleepy).execute(plan)
    on = PlanExecutor(
        registry,
        specialist_runner=sleepy,
        review_enabled=True,
        reviewer=OutputReviewer(SlowReview()),
    ).execute(plan)
    print("\n=== Fake-worker timing comparison (NOT live API latency) ===")
    print(
        f"review_off duration_ms={off.duration_ms:.2f} "
        f"quality_approved={off.task_results[0].quality_approved} "
        f"reviewer_model_calls={off.reviewer_model_calls}"
    )
    print(
        f"review_on  duration_ms={on.duration_ms:.2f} "
        f"quality_approved={on.task_results[0].quality_approved} "
        f"reviewer_model_calls={on.reviewer_model_calls}"
    )
    print(
        "note=Acceptance means assignment-fit under supplied context; "
        "it is not proof of factual correctness."
    )


if __name__ == "__main__":
    main()
