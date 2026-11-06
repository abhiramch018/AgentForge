"""Fake LLM providers for offline evaluation — never paid calls."""

from __future__ import annotations

import threading
from collections.abc import Sequence

from app.core.exceptions import LLMTimeoutError
from app.core.messages import ChatMessage
from app.evaluation.models import ScriptedTurn
from app.llm.base import LLMClient, LLMResponse, TokenUsage


def _is_review_request(messages: Sequence[ChatMessage]) -> bool:
    blob = "\n".join(f"{m.role.value}: {m.content}" for m in messages).lower()
    return "output reviewer" in blob or (
        '"decision"' in blob and "task_coverage" in blob
    )


def _is_plan_request(messages: Sequence[ChatMessage]) -> bool:
    blob = "\n".join(f"{m.role.value}: {m.content}" for m in messages).lower()
    return "planning supervisor" in blob or "lean execution plan" in blob


class EvalScriptedLLM(LLMClient):
    """Returns scripted specialist / review / plan turns with token usage."""

    def __init__(self, turns: Sequence[ScriptedTurn], *, model: str = "fake-model") -> None:
        self._turns = list(turns)
        self._model = model
        self.calls = 0
        self._lock = threading.Lock()

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        want_review = _is_review_request(messages)
        want_plan = _is_plan_request(messages)
        with self._lock:
            self.calls += 1
            turn = self._pop(want_review=want_review, want_plan=want_plan)
        usage = TokenUsage(
            prompt_tokens=turn.prompt_tokens,
            completion_tokens=turn.completion_tokens,
            total_tokens=turn.prompt_tokens + turn.completion_tokens,
        )
        return LLMResponse(content=turn.content, model=self._model, usage=usage)

    def _pop(self, *, want_review: bool, want_plan: bool) -> ScriptedTurn:
        if not self._turns:
            if want_review:
                return ScriptedTurn(
                    kind="review",
                    content=(
                        '{"decision":"accepted","feedback":"fallback accept",'
                        '"issues":[],"task_coverage":"complete",'
                        '"expected_output_fit":"strong","internal_consistency":"ok",'
                        '"evidence_support":"ok","limitations":"n/a",'
                        '"checks_supplied_evidence_only":true,'
                        '"independently_verified_facts":false}'
                    ),
                )
            return ScriptedTurn(kind="specialist", content="fallback output")

        preferred = "review" if want_review else ("plan" if want_plan else "specialist")
        for idx, turn in enumerate(self._turns):
            if turn.kind in (preferred, "any"):
                return self._turns.pop(idx)
        return self._turns.pop(0)


class TimeoutLLM(LLMClient):
    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        raise LLMTimeoutError("Simulated provider timeout for evaluation.")


class MalformedPlanLLM(LLMClient):
    """Returns invalid plan JSON then a valid repair for supervisor tests."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(content="{not-json", model="fake-model")
        # Minimal valid single-task plan for repair path.
        return LLMResponse(
            content=(
                '{"goal":"g","tasks":[{"id":"t1","description":"Do work",'
                '"role":"general","depends_on":[],"expected_output":"Done"}],'
                '"summary":"ok"}'
            ),
            model="fake-model",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )
