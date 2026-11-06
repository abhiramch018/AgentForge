"""Job lifecycle models.

Authoritative state lives in the job store (SQLite). The Huey queue only
carries ``job_id`` delivery notifications — never credentials or full
results.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # Lease expired or worker vanished; may be re-queued under retry bounds.
    INTERRUPTED = "interrupted"
    # Human approval required; no worker lease / capacity held.
    AWAITING_APPROVAL = "awaiting_approval"


TERMINAL_STATUSES = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED})
# Not claimable for execution until human decision moves the job forward.
PAUSED_STATUSES = frozenset({JobStatus.AWAITING_APPROVAL})


class JobTiming(BaseModel):
    submitted_at_ms: float
    started_at_ms: float | None = None
    finished_at_ms: float | None = None
    queue_wait_ms: float | None = None
    execute_ms: float | None = None
    total_ms: float | None = None


class JobRecord(BaseModel):
    job_id: str
    status: JobStatus
    request: dict[str, Any]
    created_at_ms: float
    updated_at_ms: float
    attempt: int = 0
    max_attempts: int = 2
    owner_id: str | None = None
    lease_expires_at_ms: float | None = None
    error_code: str | None = None
    error_message: str | None = None
    uncertainty: str | None = None
    result: dict[str, Any] | None = None
    checkpoint: dict[str, Any] | None = None
    trace_context: dict[str, str] | None = None
    timing: JobTiming


class JobSubmitResponse(BaseModel):
    api_version: str = "v1"
    job_id: str
    status: JobStatus = JobStatus.QUEUED


class JobStatusResponse(BaseModel):
    api_version: str = "v1"
    job_id: str
    status: JobStatus
    attempt: int
    max_attempts: int
    timing: JobTiming
    error_code: str | None = None
    error_message: str | None = None
    uncertainty: str | None = None
    result_available: bool = False


class JobResultResponse(BaseModel):
    api_version: str = "v1"
    job_id: str
    status: JobStatus
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    uncertainty: str | None = None
    timing: JobTiming


class JobStatusCounts(BaseModel):
    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    interrupted: int = 0
    awaiting_approval: int = 0


class JobListItem(BaseModel):
    job_id: str
    status: JobStatus
    attempt: int
    max_attempts: int
    created_at_ms: float
    updated_at_ms: float
    timing: JobTiming
    error_code: str | None = None
    error_message: str | None = None
    goal_preview: str | None = None
    result_available: bool = False
    review_enabled: bool | None = None
    memory_enabled: bool | None = None


class JobListResponse(BaseModel):
    api_version: str = "v1"
    items: list[JobListItem]
    count: int
    status_counts: JobStatusCounts
