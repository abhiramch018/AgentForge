"""Results produced when a plan is executed.

Status meanings
---------------
- ``succeeded``: eligible for downstream use. If ``quality_approved`` is
  true, a reviewer accepted the output. If ``quality_approved`` is false,
  review was bypassed and this is an execution-only success — not a
  quality approval.
- ``rejected``: revisions were exhausted while the reviewer still required
  changes. Drafts remain in ``attempts`` but are not published downstream.
- ``failed``: execution error, reviewer error, or ``cannot_assess``.
- ``timed_out``: provider timeout reported. This does not prove underlying
  work stopped.
- ``skipped``: a dependency was not eligible for downstream use.
"""

from enum import Enum

from pydantic import BaseModel, Field

from app.llm.base import TokenUsage
from app.orchestration.review import AttemptRecord, ReviewPolicy


class TaskStatus(str, Enum):
    """Lifecycle status for one planned task."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"
    REJECTED = "rejected"


class ExecutionStatus(str, Enum):
    """Overall status for one plan run."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TaskResult(BaseModel):
    """Structured outcome for one task, including review history."""

    task_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    agent_name: str | None = None
    status: TaskStatus
    output: str | None = None
    error: str | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    model: str | None = None
    usage: TokenUsage | None = None
    attempts: list[AttemptRecord] = Field(default_factory=list)
    review_policy: ReviewPolicy = ReviewPolicy.BYPASSED_REVIEW_DISABLED
    review_bypass_reason: str | None = "review_disabled"
    quality_approved: bool = False
    review_decision_count: int = Field(default=0, ge=0)
    reviewer_model_calls: int = Field(default=0, ge=0)
    reviewer_repair_calls: int = Field(default=0, ge=0)
    revision_count: int = Field(default=0, ge=0)


class ExecutionResult(BaseModel):
    """Structured outcome for an entire plan run."""

    goal: str = Field(min_length=1)
    status: ExecutionStatus
    task_results: list[TaskResult]
    duration_ms: float = Field(ge=0)
    succeeded_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    timed_out_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    rejected_count: int = Field(default=0, ge=0)
    review_decision_count: int = Field(default=0, ge=0)
    reviewer_model_calls: int = Field(default=0, ge=0)
    reviewer_repair_calls: int = Field(default=0, ge=0)
    revision_count: int = Field(default=0, ge=0)
    review_enabled: bool = False
