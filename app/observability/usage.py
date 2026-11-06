"""Accurate usage aggregation — missing usage is unknown, not zero."""

from __future__ import annotations

from typing import Any

from app.llm.base import TokenUsage
from app.observability.models import UsageBucket, UsageCoverage, UsageSummary
from app.orchestration.models import PlanResult


def aggregate_run_usage(
    *,
    planning: PlanResult | None,
    execution: Any | None,
    embedding_calls: int = 0,
    embedding_usage_known: bool = False,
) -> UsageSummary:
    """Aggregate reported tokens without double-counting task-level copies.

    Specialist tokens are taken from ``attempts[].usage`` only. ``TaskResult.usage``
    is treated as a convenience copy of the last attempt and is not added again.
    Reviewer tokens come from ``attempts[].review.usage``.
    """

    buckets: list[UsageBucket] = []
    notes: list[str] = []

    if planning is not None:
        buckets.append(
            _bucket_from_usage(
                "planning",
                planning.usage,
                model=planning.model,
                calls=max(1, planning.llm_calls),
            )
        )

    if execution is not None:
        for task in execution.task_results:
            for attempt in task.attempts:
                buckets.append(
                    _bucket_from_usage(
                        f"specialist:{task.task_id}:{attempt.kind.value}:{attempt.index}",
                        attempt.usage,
                        model=attempt.model,
                        calls=1,
                    )
                )
                if attempt.review is not None:
                    buckets.append(
                        _bucket_from_usage(
                            f"reviewer:{task.task_id}:{attempt.index}",
                            attempt.review.usage,
                            model=attempt.review.model,
                            calls=max(1, attempt.review.reviewer_model_calls),
                        )
                    )
            if task.status.value == "timed_out":
                notes.append(
                    f"task {task.task_id} timed out; usage may be incomplete."
                )

    if embedding_calls:
        if embedding_usage_known:
            buckets.append(
                UsageBucket(
                    name="embeddings",
                    calls_with_usage=embedding_calls,
                    calls_missing_usage=0,
                )
            )
        else:
            buckets.append(
                UsageBucket(
                    name="embeddings",
                    calls_with_usage=0,
                    calls_missing_usage=embedding_calls,
                )
            )
            notes.append(
                "Embedding calls occurred without provider token usage; "
                "treated as missing, not zero."
            )

    prompt = _sum_optional(b.prompt_tokens for b in buckets)
    completion = _sum_optional(b.completion_tokens for b in buckets)
    total = _sum_optional(b.total_tokens for b in buckets)
    # Prefer summing prompt+completion when both known and total missing.
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion

    missing = sum(b.calls_missing_usage for b in buckets)
    present = sum(b.calls_with_usage for b in buckets)
    if present == 0 and missing == 0:
        coverage = UsageCoverage.UNAVAILABLE
        notes.append("No model calls recorded usage fields.")
    elif missing > 0:
        coverage = UsageCoverage.PARTIAL
        notes.append(
            f"{missing} call(s) lacked usage; totals are incomplete, not zero-filled."
        )
    else:
        coverage = UsageCoverage.COMPLETE

    notes.append(
        "Task-level usage fields are not double-counted; attempt/review rows are source of truth."
    )
    return UsageSummary(
        coverage=coverage,
        buckets=buckets,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        notes=notes,
    )


def _bucket_from_usage(
    name: str,
    usage: TokenUsage | None,
    *,
    model: str | None,
    calls: int,
) -> UsageBucket:
    if usage is None:
        return UsageBucket(
            name=name,
            model=model,
            calls_with_usage=0,
            calls_missing_usage=calls,
        )
    return UsageBucket(
        name=name,
        model=model,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        calls_with_usage=calls,
        calls_missing_usage=0,
    )


def _sum_optional(values: Any) -> int | None:
    nums = [v for v in values if isinstance(v, int)]
    if not nums:
        return None
    return sum(nums)
