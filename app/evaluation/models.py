"""Evaluation contracts — criteria defined before runs, not after."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class CaseSplit(str, Enum):
    DEV = "dev"
    HELD_OUT = "held_out"


class CriterionKind(str, Enum):
    """Deterministic checks preferred; rubrics only where judgment is needed."""

    STATUS_IN = "status_in"
    REQUIRED_SUBSTRINGS = "required_substrings"
    FORBIDDEN_SUBSTRINGS = "forbidden_substrings"
    UNCERTAINTY_ACKNOWLEDGED = "uncertainty_acknowledged"
    TASK_COUNT = "task_count"
    QUALITY_APPROVED = "quality_approved"
    APPROVAL_REQUIRED = "approval_required"
    NO_SIDE_EFFECT_WITHOUT_APPROVAL = "no_side_effect_without_approval"
    MAX_REVISIONS = "max_revisions"
    RUBRIC_HINT = "rubric_hint"  # human-readable; scored offline via deterministic proxies


class EvalCriterion(BaseModel):
    id: str
    kind: CriterionKind
    weight: float = Field(default=1.0, gt=0)
    description: str
    params: dict[str, Any] = Field(default_factory=dict)


class ExpectedBehavior(BaseModel):
    """Declared before evaluation — not reverse-engineered from outputs."""

    summary: str
    criteria: list[EvalCriterion]


class ScriptedTurn(BaseModel):
    """Offline fake-provider reply. Not a live model transcript."""

    kind: Literal["specialist", "review", "plan", "any"] = "any"
    content: str
    prompt_tokens: int = 10
    completion_tokens: int = 5


class EvalCase(BaseModel):
    id: str
    split: CaseSplit
    title: str
    tags: list[str] = Field(default_factory=list)
    goal: str | None = None
    plan: dict[str, Any] | None = None
    protected_action: dict[str, Any] | None = None
    memory_seed: list[str] = Field(default_factory=list)
    scripted_turns: list[ScriptedTurn] = Field(default_factory=list)
    expected: ExpectedBehavior
    notes: list[str] = Field(default_factory=list)


class EvalDataset(BaseModel):
    version: str
    description: str
    retrieval_label: str = (
        "HashingEmbedder — deterministic bag-of-tokens; not production semantic quality"
    )
    cases: list[EvalCase]


class CriterionScore(BaseModel):
    criterion_id: str
    kind: CriterionKind
    passed: bool
    score: float = Field(ge=0, le=1)
    detail: str
    method: Literal["deterministic", "rubric_proxy", "llm_judge"] = "deterministic"


class CaseTelemetry(BaseModel):
    total_ms: float | None = None
    plan_ms: float | None = None
    execute_ms: float | None = None
    model_calls: int | None = None
    revision_count: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    usage_coverage: str | None = None
    cost_usd: float | None = None
    cost_coverage: str | None = None
    cost_kind: Literal["estimate"] | None = None
    trace_id: str | None = None


class CaseOutcome(BaseModel):
    case_id: str
    split: CaseSplit
    config_id: str
    mode: Literal["mocked", "live"]
    success: bool
    error: str | None = None
    quality_score: float | None = None
    criteria: list[CriterionScore] = Field(default_factory=list)
    telemetry: CaseTelemetry = Field(default_factory=CaseTelemetry)
    status: str | None = None
    notes: list[str] = Field(default_factory=list)


class EvalConfig(BaseModel):
    id: str
    review_enabled: bool
    memory_enabled: bool
    max_revisions: int = 1
    max_concurrency: int = 2
    description: str = ""


class FailureCaseResult(BaseModel):
    id: str
    title: str
    passed: bool
    detail: str
    category: Literal[
        "timeout",
        "malformed",
        "storage",
        "delivery",
        "crash",
        "stream",
        "approval",
        "injection_boundary",
    ]
    enforcement: Literal["deterministic_boundary", "prompt_injection_resistance_unproven"]


class EvalManifest(BaseModel):
    dataset_version: str
    mode: Literal["mocked", "live"]
    started_at: str
    finished_at: str | None = None
    model_identifier: str
    code_version: str | None = None
    configs: list[EvalConfig]
    budgets: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class EvalReport(BaseModel):
    api_version: Literal["v1"] = "v1"
    manifest: EvalManifest
    case_outcomes: list[CaseOutcome]
    failure_outcomes: list[FailureCaseResult]
    summary: dict[str, Any] = Field(default_factory=dict)
