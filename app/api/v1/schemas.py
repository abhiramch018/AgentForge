"""Versioned HTTP contracts for the orchestration service."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.approval.models import ApprovalRecord, ProtectedAction
from app.memory.models import MemoryRecord, MemoryRetrievalMetrics
from app.observability.models import ObservabilitySummary
from app.orchestration.exceptions import PlanValidationError
from app.orchestration.execution import ExecutionResult, ExecutionStatus
from app.orchestration.models import ExecutionPlan, PlanResult, PlanTask, SpecialistRole
from app.orchestration.validation import validate_plan


class TaskInput(PlanTask):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=20000)
    expected_output: str = Field(min_length=1, max_length=20000)
    depends_on: list[str] = Field(default_factory=list, max_length=12)


class PlanInput(ExecutionPlan):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=20000)
    tasks: list[TaskInput] = Field(min_length=1, max_length=12)
    summary: str | None = Field(default=None, max_length=20000)

    @model_validator(mode="after")
    def valid_graph(self) -> "PlanInput":
        try:
            validate_plan(self, max_tasks=12)
        except PlanValidationError as exc:
            raise ValueError(str(exc)) from exc
        return self


class RunRequest(BaseModel):
    """Exactly one of ``goal`` or ``plan`` must be provided.

    Tools are not client-configurable in M7; the service keeps tools disabled.
    """

    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"] = "v1"
    goal: str | None = Field(default=None, min_length=1, max_length=20000)
    plan: PlanInput | None = None
    review_enabled: bool = Field(default=False, strict=True)
    review_roles: list[SpecialistRole] | None = Field(default=None, max_length=6)
    max_revisions: int = Field(default=1, ge=0, le=3, strict=True)
    max_concurrency: int = Field(default=4, ge=1, le=32, strict=True)
    memory_enabled: bool = Field(default=True, strict=True)
    # M10: optional protected side effect (requires background job + human approval)
    protected_action: ProtectedAction | None = None

    @model_validator(mode="after")
    def one_input(self) -> "RunRequest":
        if (self.goal is None) == (self.plan is None):
            raise ValueError("Provide exactly one of goal or plan.")
        if self.goal is not None:
            cleaned = self.goal.strip()
            if not cleaned:
                raise ValueError("Goal must not be blank.")
            self.goal = cleaned
        return self


class RunTiming(BaseModel):
    total_ms: float = Field(ge=0)
    plan_ms: float | None = Field(default=None, ge=0)
    execute_ms: float = Field(ge=0)
    memory: MemoryRetrievalMetrics | None = None


class RunResponse(BaseModel):
    api_version: Literal["v1"] = "v1"
    run_id: str
    status: ExecutionStatus
    plan: ExecutionPlan
    planning: PlanResult | None = None
    execution: ExecutionResult
    timing: RunTiming
    effective_max_concurrency: int = Field(ge=1)
    memory_enabled: bool = True
    artifact: dict[str, Any] | None = None
    approval_id: str | None = None
    observability: ObservabilitySummary | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    api_version: Literal["v1"] = "v1"
    run_id: str | None = None
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyResponse(BaseModel):
    """Readiness: storage and runtime dependencies, not process liveness."""

    status: Literal["ready", "not_ready"] = "ready"
    checks: dict[str, bool] = Field(default_factory=dict)
    offline_demo: bool = False
    detail: str | None = None


class MetaResponse(BaseModel):
    api_version: Literal["v1"] = "v1"
    review_default_enabled: bool = False
    tools_enabled: bool = False
    auth_required: bool
    capacity_scope: Literal["single_process"] = "single_process"
    workspace_scope: Literal["single_workspace"] = "single_workspace"
    workspace_id: str = "default"
    memory_enabled_default: bool = True
    max_inflight_runs: int
    max_inflight_tasks: int
    max_run_concurrency: int
    notes: list[str] = Field(
        default_factory=lambda: [
            "POST /v1/runs waits synchronously until the run finishes.",
            "Client disconnect or read timeout may leave an uncertain outcome; "
            "the service does not automatically cancel worker threads.",
            "Capacity limits apply to this process only, not across replicas.",
            "quality_approved=true means reviewer acceptance; unreviewed "
            "success is not quality-approved.",
            "Memory is single-workspace when using a shared bearer token.",
        ]
    )


class MemoryListResponse(BaseModel):
    api_version: Literal["v1"] = "v1"
    workspace_id: str
    items: list[MemoryRecord]
    count: int


class MemoryDeleteResponse(BaseModel):
    api_version: Literal["v1"] = "v1"
    workspace_id: str
    deleted: int = 0
    memory_id: str | None = None


class ApprovalListResponse(BaseModel):
    api_version: Literal["v1"] = "v1"
    workspace_id: str
    items: list[ApprovalRecord]
    count: int


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str = Field(min_length=16, max_length=128)
    note: str | None = Field(default=None, max_length=2000)


class ApprovalRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=2000)


class ApprovalReviseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ProtectedAction
    note: str | None = Field(default=None, max_length=2000)
