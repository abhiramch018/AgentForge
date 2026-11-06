"""Observability response models."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class UsageCoverage(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class UsageBucket(BaseModel):
    """One accounting bucket (planning, specialist attempts, reviewer, …)."""

    name: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    model: str | None = None
    calls_with_usage: int = 0
    calls_missing_usage: int = 0


class UsageSummary(BaseModel):
    coverage: UsageCoverage
    buckets: list[UsageBucket] = Field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    notes: list[str] = Field(default_factory=list)


class CostLine(BaseModel):
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    input_usd: float | None = None
    output_usd: float | None = None
    line_usd: float | None = None
    priced: bool


class CostEstimate(BaseModel):
    """Labeled estimate — never presented as a live invoice."""

    kind: Literal["estimate"] = "estimate"
    currency: str = "USD"
    as_of: str
    provenance: str
    unit: str
    coverage: UsageCoverage
    total_usd: float | None = None
    lines: list[CostLine] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LatencyBreakdown(BaseModel):
    queue_wait_ms: float | None = None
    planning_ms: float | None = None
    execution_ms: float | None = None
    approval_wait_ms: float | None = None
    total_ms: float | None = None
    notes: list[str] = Field(default_factory=list)


class ObservabilitySummary(BaseModel):
    usage: UsageSummary
    cost: CostEstimate
    latency: LatencyBreakdown | None = None
    trace_id: str | None = None


class ReplayEvent(BaseModel):
    event_id: int
    event_type: str
    created_at_ms: float
    delta_ms: float | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ReplayTimeline(BaseModel):
    """Recorded timeline playback — not a workflow re-execution."""

    api_version: Literal["v1"] = "v1"
    mode: Literal["timeline_playback"] = "timeline_playback"
    job_id: str
    event_count: int
    events: list[ReplayEvent]
    latency: LatencyBreakdown
    notes: list[str] = Field(
        default_factory=lambda: [
            "This is timeline playback from durable events.",
            "It does not call models, execute tools, or re-apply approvals.",
            "A true rerun must submit a new job under current permissions.",
        ]
    )
