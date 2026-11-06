"""Structured LLM reviewer for specialist outputs.

Thread safety: ``OutputReviewer.review`` keeps all request state in local
variables. The instance only stores immutable configuration and an
``LLMClient``. Concurrent reviews are safe only to the extent that the
shared client allows concurrent ``generate`` calls (same assumption as M5).

The reviewer never advertises or executes tools.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.core.messages import ChatMessage, MessageRole
from app.llm.base import LLMClient, LLMResponse, TokenUsage
from app.orchestration.exceptions import (
    ReviewConfigurationError,
    ReviewGenerationError,
    format_validation_error,
)
from app.orchestration.models import PlanTask
from app.orchestration.review import ReviewDecision, ReviewResult
from app.orchestration.review_prompts import (
    REVIEWER_SYSTEM_INSTRUCTION,
    build_review_repair_message,
    build_review_user_message,
)

logger = logging.getLogger(__name__)

_MAX_REPAIR_OUTPUT_CHARS = 4000


class OutputReviewer:
    """Judge one specialist output against its task assignment."""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        max_repair_attempts: int = 1,
        system_instruction: str | None = None,
    ) -> None:
        if not isinstance(llm_client, LLMClient):
            raise ReviewConfigurationError("llm_client must be an LLMClient.")
        if isinstance(max_repair_attempts, bool) or not isinstance(
            max_repair_attempts,
            int,
        ):
            raise ReviewConfigurationError(
                "max_repair_attempts must be a non-negative integer."
            )
        if max_repair_attempts < 0:
            raise ReviewConfigurationError(
                "max_repair_attempts must be a non-negative integer."
            )
        instruction = (
            REVIEWER_SYSTEM_INSTRUCTION
            if system_instruction is None
            else system_instruction.strip()
        )
        if not instruction:
            raise ReviewConfigurationError("system_instruction must not be blank.")
        self._llm_client = llm_client
        self.max_repair_attempts = max_repair_attempts
        self.system_instruction = instruction

    def review(
        self,
        *,
        goal: str,
        task: PlanTask,
        specialist_output: str,
        dependency_outputs: dict[str, str] | None = None,
    ) -> ReviewResult:
        """Return a structured review. Uses no tools."""

        if not specialist_output.strip():
            raise ReviewGenerationError("specialist_output must not be blank.")
        deps = dict(dependency_outputs or {})
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=self.system_instruction),
            ChatMessage(
                role=MessageRole.USER,
                content=build_review_user_message(
                    goal=goal,
                    task=task,
                    specialist_output=specialist_output,
                    dependency_outputs=deps,
                ),
            ),
        ]
        max_attempts = self.max_repair_attempts + 1
        usage: TokenUsage | None = None
        model: str | None = None
        last_errors = "No review was produced."
        started = time.perf_counter()
        model_calls = 0
        repair_calls = 0

        for attempt in range(1, max_attempts + 1):
            response = self._llm_client.generate(messages)
            model_calls += 1
            if attempt > 1:
                repair_calls += 1
            usage = _combine_usage(usage, response.usage)
            model = response.model
            try:
                draft = self._parse_response(response)
            except (ReviewGenerationError, ValidationError) as exc:
                if isinstance(exc, ValidationError):
                    last_errors = format_validation_error(exc)
                else:
                    last_errors = str(exc)
                logger.info(
                    "review_parse_failed attempt=%s error=%s",
                    attempt,
                    last_errors,
                )
                if attempt >= max_attempts:
                    break
                messages.append(
                    ChatMessage(
                        role=MessageRole.ASSISTANT,
                        content=response.content or "",
                        tool_calls=list(response.tool_calls),
                    )
                )
                messages.append(
                    ChatMessage(
                        role=MessageRole.USER,
                        content=build_review_repair_message(
                            previous_output=_truncate(
                                response.content or "",
                                _MAX_REPAIR_OUTPUT_CHARS,
                            ),
                            errors=last_errors,
                        ),
                    )
                )
                continue

            duration_ms = (time.perf_counter() - started) * 1000
            return ReviewResult(
                decision=draft.decision,
                feedback=draft.feedback,
                issues=list(draft.issues),
                task_coverage=draft.task_coverage,
                expected_output_fit=draft.expected_output_fit,
                internal_consistency=draft.internal_consistency,
                evidence_support=draft.evidence_support,
                limitations=draft.limitations,
                checks_supplied_evidence_only=True,
                independently_verified_facts=False,
                model=model,
                usage=usage,
                duration_ms=duration_ms,
                reviewer_model_calls=model_calls,
                reviewer_repair_calls=repair_calls,
            ).snapshot()

        raise ReviewGenerationError(
            f"Could not produce a valid review after {max_attempts} model "
            f"call(s). Last errors: {last_errors}"
        )

    def _parse_response(self, response: LLMResponse) -> _DraftReview:
        if response.tool_calls:
            raise ReviewGenerationError(
                "The reviewer requested a tool. Reviewing does not use tools."
            )
        if response.content is None or not response.content.strip():
            raise ReviewGenerationError("The reviewer returned no text.")
        payload = _extract_json_object(response.content)
        return _DraftReview.model_validate(payload)


class _DraftReview(BaseModel):
    decision: ReviewDecision
    feedback: str = Field(min_length=1)
    issues: list[str] = Field(default_factory=list)
    task_coverage: str = Field(min_length=1)
    expected_output_fit: str = Field(min_length=1)
    internal_consistency: str = Field(min_length=1)
    evidence_support: str = Field(min_length=1)
    limitations: str = Field(min_length=1)
    checks_supplied_evidence_only: bool = True
    independently_verified_facts: bool = False


def _extract_json_object(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        raise ReviewGenerationError("The reviewer reply did not contain a JSON object.")
    snippet = candidate[start : end + 1]
    try:
        payload = json.loads(snippet)
    except json.JSONDecodeError as exc:
        raise ReviewGenerationError("The reviewer reply was not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ReviewGenerationError("The reviewer reply JSON must be an object.")
    return payload


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]..."


def _combine_usage(current: TokenUsage | None, added: TokenUsage | None) -> TokenUsage | None:
    if added is None:
        return current
    if current is None:
        return added.model_copy()
    return TokenUsage(
        prompt_tokens=_sum_counts(current.prompt_tokens, added.prompt_tokens),
        completion_tokens=_sum_counts(
            current.completion_tokens,
            added.completion_tokens,
        ),
        total_tokens=_sum_counts(current.total_tokens, added.total_tokens),
    )


def _sum_counts(left: int | None, right: int | None) -> int | None:
    if left is None or right is None:
        return None
    return left + right
