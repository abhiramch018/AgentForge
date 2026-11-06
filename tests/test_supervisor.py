"""Supervisor planning tests.

All LLM replies are faked. These tests never call a paid API.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from app.core.exceptions import LLMProviderError, LLMTimeoutError
from app.core.messages import ChatMessage, ToolCall
from app.llm.base import LLMClient, LLMResponse, TokenUsage
from app.orchestration import (
    PlanConfigurationError,
    PlanGenerationError,
    PlanRequestError,
    SpecialistRole,
    Supervisor,
    execution_order,
)


class ScriptedLLMClient(LLMClient):
    """Returns prepared responses in order and records each call."""

    def __init__(self, responses: Sequence[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[list[ChatMessage]] = []
        self.tools_seen: list[list[object]] = []

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        self.tools_seen.append(list(tools or []))
        if not self._responses:
            raise AssertionError("ScriptedLLMClient has no remaining responses.")
        return self._responses.pop(0)


def _json_response(payload: dict[str, object], *, model: str = "fake-model") -> LLMResponse:
    return LLMResponse(
        content=json.dumps(payload),
        model=model,
        usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
    )


def _simple_plan_payload() -> dict[str, object]:
    return {
        "summary": "One task is enough.",
        "tasks": [
            {
                "id": "explain_concept",
                "description": "Explain microservices in plain language.",
                "role": "general",
                "depends_on": [],
                "expected_output": "A short plain-language explanation.",
            }
        ],
    }


def _mixed_plan_payload() -> dict[str, object]:
    return {
        "summary": "Research in parallel, then write.",
        "tasks": [
            {
                "id": "research_a",
                "description": "Gather pricing for vendor A.",
                "role": "researcher",
                "depends_on": [],
                "expected_output": "Notes on vendor A pricing.",
            },
            {
                "id": "research_b",
                "description": "Gather pricing for vendor B.",
                "role": "researcher",
                "depends_on": [],
                "expected_output": "Notes on vendor B pricing.",
            },
            {
                "id": "draft_summary",
                "description": "Write a one-page leadership summary from both research tasks.",
                "role": "writer",
                "depends_on": ["research_a", "research_b"],
                "expected_output": "One-page summary for leadership.",
            },
        ],
    }


def test_supervisor_rejects_invalid_configuration() -> None:
    client = ScriptedLLMClient([_json_response(_simple_plan_payload())])

    with pytest.raises(PlanConfigurationError, match="llm_client"):
        Supervisor(object())  # type: ignore[arg-type]

    with pytest.raises(PlanConfigurationError, match="max_repair_attempts"):
        Supervisor(client, max_repair_attempts=-1)

    with pytest.raises(PlanConfigurationError, match="max_tasks"):
        Supervisor(client, max_tasks=0)


def test_blank_goal_never_calls_the_model() -> None:
    client = ScriptedLLMClient([_json_response(_simple_plan_payload())])
    supervisor = Supervisor(client)

    with pytest.raises(PlanRequestError, match="blank"):
        supervisor.create_plan("   ")

    assert client.calls == []


def test_happy_path_uses_exactly_one_llm_call() -> None:
    client = ScriptedLLMClient([_json_response(_simple_plan_payload())])
    supervisor = Supervisor(client)

    result = supervisor.create_plan("Explain microservices in simple terms.")

    assert result.llm_calls == 1
    assert result.repaired is False
    assert result.duration_ms >= 0
    assert result.model == "fake-model"
    assert result.usage is not None
    assert result.usage.total_tokens == 30
    assert result.plan.goal == "Explain microservices in simple terms."
    assert len(result.plan.tasks) == 1
    assert result.plan.tasks[0].role is SpecialistRole.GENERAL
    assert client.tools_seen == [[]]


def test_simple_request_scenario_stays_single_task() -> None:
    """Representative simple goal: lean plan, no fake dependencies."""

    client = ScriptedLLMClient([_json_response(_simple_plan_payload())])
    result = Supervisor(client).create_plan("Explain microservices in simple terms.")

    assert result.llm_calls == 1
    assert len(result.plan.tasks) == 1
    assert result.plan.tasks[0].depends_on == []


def test_independent_and_dependent_task_scenario() -> None:
    """Representative mixed goal: parallel research, then dependent writing."""

    client = ScriptedLLMClient([_json_response(_mixed_plan_payload())])
    result = Supervisor(client).create_plan(
        "Research competitor pricing for vendors A and B, then draft a one-page summary."
    )

    assert result.llm_calls == 1
    ids = {task.id: task for task in result.plan.tasks}
    assert ids["research_a"].depends_on == []
    assert ids["research_b"].depends_on == []
    assert set(ids["draft_summary"].depends_on) == {"research_a", "research_b"}
    assert execution_order(result.plan) == [
        "research_a",
        "research_b",
        "draft_summary",
    ]


def test_repair_succeeds_on_second_llm_call() -> None:
    invalid = {"tasks": [{"id": "a", "description": "A", "role": "general",
                          "depends_on": ["missing"], "expected_output": "Out"}]}
    client = ScriptedLLMClient(
        [
            _json_response(invalid),
            _json_response(_simple_plan_payload()),
        ]
    )
    result = Supervisor(client, max_repair_attempts=2).create_plan("Explain microservices.")

    assert result.llm_calls == 2
    assert result.repaired is True
    assert result.usage is not None
    assert result.usage.total_tokens == 60
    assert len(client.calls) == 2
    assert "unknown id" in client.calls[1][-1].content


def test_repair_budget_exhausted_raises_plan_generation_error() -> None:
    bad = LLMResponse(content="not json at all", model="fake-model")
    client = ScriptedLLMClient([bad, bad, bad])
    supervisor = Supervisor(client, max_repair_attempts=2)

    with pytest.raises(PlanGenerationError, match="Could not produce a valid plan"):
        supervisor.create_plan("Build a plan.")

    assert len(client.calls) == 3


def test_goal_from_model_is_overwritten_by_caller_goal() -> None:
    payload = _simple_plan_payload()
    payload["goal"] = "A different goal the model invented"
    client = ScriptedLLMClient([_json_response(payload)])

    result = Supervisor(client).create_plan("Caller goal stays.")

    assert result.plan.goal == "Caller goal stays."


def test_fenced_json_is_accepted() -> None:
    body = json.dumps(_simple_plan_payload())
    client = ScriptedLLMClient(
        [LLMResponse(content=f"Here you go:\n```json\n{body}\n```", model="fake-model")]
    )

    result = Supervisor(client).create_plan("Explain microservices.")

    assert result.llm_calls == 1
    assert len(result.plan.tasks) == 1


def test_tool_call_in_planning_response_is_rejected_then_repaired() -> None:
    first = LLMResponse(
        content=None,
        model="fake-model",
        tool_calls=[ToolCall(id="1", name="calculator", arguments={})],
    )
    client = ScriptedLLMClient([first, _json_response(_simple_plan_payload())])

    result = Supervisor(client).create_plan("Explain microservices.")

    assert result.llm_calls == 2
    assert result.repaired is True


def test_provider_errors_propagate() -> None:
    class ExplodingClient(LLMClient):
        def generate(
            self,
            messages: Sequence[ChatMessage],
            tools: Sequence[object] | None = None,
        ) -> LLMResponse:
            raise LLMTimeoutError("timed out")

    with pytest.raises(LLMTimeoutError):
        Supervisor(ExplodingClient()).create_plan("Any goal")

    class FailingClient(LLMClient):
        def generate(
            self,
            messages: Sequence[ChatMessage],
            tools: Sequence[object] | None = None,
        ) -> LLMResponse:
            raise LLMProviderError("provider failed")

    with pytest.raises(LLMProviderError):
        Supervisor(FailingClient()).create_plan("Any goal")


def test_missing_usage_stays_none() -> None:
    client = ScriptedLLMClient(
        [
            LLMResponse(
                content=json.dumps(_simple_plan_payload()),
                model="fake-model",
                usage=None,
            )
        ]
    )

    result = Supervisor(client).create_plan("Explain microservices.")

    assert result.usage is None
    assert result.model == "fake-model"


def test_orchestration_package_does_not_import_the_openai_sdk() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "orchestration"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "openai"
                    assert not alias.name.startswith("openai.")
            if isinstance(node, ast.ImportFrom):
                assert node.module != "openai"
                assert not (node.module or "").startswith("openai.")
