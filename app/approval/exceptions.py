"""Approval control-flow and policy errors."""

from __future__ import annotations

from typing import Any


class ApprovalError(Exception):
    """Base approval error."""


class ApprovalRequired(ApprovalError):
    """Trusted gate paused execution pending human authorization.

    Not an LLM or review failure. The worker must persist the proposal and
    release capacity without treating this as a failed run.
    """

    def __init__(
        self,
        *,
        approval_id: str,
        job_id: str,
        fingerprint: str,
        checkpoint: dict[str, Any],
    ) -> None:
        super().__init__(
            f"Approval required for job {job_id} (approval_id={approval_id})."
        )
        self.approval_id = approval_id
        self.job_id = job_id
        self.fingerprint = fingerprint
        self.checkpoint = checkpoint


class ApprovalConflict(ApprovalError):
    """Duplicate or conflicting decision."""


class ApprovalExpired(ApprovalError):
    """Proposal TTL elapsed."""


class FingerprintMismatch(ApprovalError):
    """Decision fingerprint does not match the stored proposal."""
