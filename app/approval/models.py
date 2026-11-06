"""Approval proposal models.

Human authorization is enforced in trusted application code. The model cannot
authorize itself. M6 ``quality_approved`` is reviewer acceptance — not human
authorization for side effects.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


class ProtectedAction(BaseModel):
    """Narrowly scoped demo action: write a text artifact under a root dir.

    Arbitrary shell and unrestricted external actions stay disabled.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["write_artifact"] = "write_artifact"
    relative_path: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1, max_length=100_000)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("relative_path")
    @classmethod
    def path_must_be_relative_safe(cls, value: str) -> str:
        cleaned = value.strip().replace("\\", "/")
        if not cleaned or cleaned.startswith("/") or cleaned.startswith("~"):
            raise ValueError("relative_path must be a relative path.")
        parts = cleaned.split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise ValueError("relative_path must not contain '.' or '..' segments.")
        if not cleaned.endswith(".txt") and not cleaned.endswith(".md"):
            raise ValueError("relative_path must end with .txt or .md.")
        return cleaned


class ProposalFingerprint:
    """Canonical fingerprint binding approval to exact proposal bytes."""

    @staticmethod
    def of(action: ProtectedAction, *, job_id: str, task_id: str | None) -> str:
        payload = {
            "job_id": job_id,
            "task_id": task_id,
            "action": action.model_dump(mode="json"),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ApprovalRecord(BaseModel):
    approval_id: str
    job_id: str
    workspace_id: str
    status: ApprovalStatus
    action: ProtectedAction
    fingerprint: str
    reason: str
    risks: list[str] = Field(default_factory=list)
    originating_task_id: str | None = None
    created_at_ms: float
    updated_at_ms: float
    expires_at_ms: float
    decided_at_ms: float | None = None
    decided_by: str | None = None
    decision_note: str | None = None
    supersedes_id: str | None = None
    checkpoint: dict[str, Any] = Field(default_factory=dict)
