"""Build ObservabilitySummary for a completed run response."""

from __future__ import annotations

from typing import Any

from app.observability.models import LatencyBreakdown, ObservabilitySummary
from app.observability.pricing import PricingTable, estimate_cost, load_fixture_pricing
from app.observability.tracing import current_trace_id
from app.observability.usage import aggregate_run_usage
from app.orchestration.models import PlanResult


def build_observability_summary(
    *,
    planning: PlanResult | None,
    execution: Any,
    timing_total_ms: float | None,
    timing_plan_ms: float | None,
    timing_execute_ms: float | None,
    embedding_calls: int = 0,
    pricing: PricingTable | None = None,
) -> ObservabilitySummary:
    usage = aggregate_run_usage(
        planning=planning,
        execution=execution,
        embedding_calls=embedding_calls,
        embedding_usage_known=False,
    )
    table = pricing or load_fixture_pricing()
    cost = estimate_cost(usage, table)
    latency = LatencyBreakdown(
        planning_ms=timing_plan_ms,
        execution_ms=timing_execute_ms,
        total_ms=timing_total_ms,
        notes=[
            "In-process timing from the orchestration service.",
            "Queue wait / approval wait appear on job replay timelines.",
        ],
    )
    return ObservabilitySummary(
        usage=usage,
        cost=cost,
        latency=latency,
        trace_id=current_trace_id(),
    )
