"""Trusted approval gate — model output cannot authorize side effects."""

from __future__ import annotations

import logging
from typing import Any

from app.approval.actions import ArtifactWriteResult, execute_write_artifact
from app.approval.exceptions import (
    ApprovalConflict,
    ApprovalExpired,
    ApprovalRequired,
    FingerprintMismatch,
)
from app.approval.models import (
    ApprovalRecord,
    ApprovalStatus,
    ProtectedAction,
    ProposalFingerprint,
)
from app.approval.store import ApprovalStore, ApprovalStoreError

logger = logging.getLogger(__name__)

DEFAULT_RISKS = [
    "Writes a file under the configured artifact root.",
    "Approval does not guarantee exactly-once side effects under crashes.",
    "Shared bearer token means shared-operator authorization, not individual attribution.",
]


class ApprovalService:
    def __init__(
        self,
        store: ApprovalStore,
        *,
        workspace_id: str,
        artifact_root: str,
        ttl_ms: float = 3_600_000.0,
        decided_by_label: str = "shared_operator",
    ) -> None:
        self.store = store
        self.workspace_id = workspace_id
        self.artifact_root = artifact_root
        self.ttl_ms = ttl_ms
        self.decided_by_label = decided_by_label

    def request_or_resume(
        self,
        *,
        job_id: str,
        action: ProtectedAction,
        checkpoint: dict[str, Any],
        task_id: str | None = None,
        approved_fingerprint: str | None = None,
    ) -> ArtifactWriteResult:
        """Execute only when a matching approval exists; otherwise pause.

        Enforced here in trusted code — not via memory, deps, or tool args.
        """

        self.store.expire_due()
        fingerprint = ProposalFingerprint.of(
            action, job_id=job_id, task_id=task_id
        )

        if approved_fingerprint is not None:
            if approved_fingerprint != fingerprint:
                raise FingerprintMismatch(
                    "Resume fingerprint does not match the protected action."
                )
            return execute_write_artifact(action, output_root=self.artifact_root)

        # Look for an already-approved proposal for this job+fingerprint.
        for record in self.store.list(workspace_id=self.workspace_id, limit=200):
            if (
                record.job_id == job_id
                and record.fingerprint == fingerprint
                and record.status is ApprovalStatus.APPROVED
            ):
                return execute_write_artifact(action, output_root=self.artifact_root)

        pending = [
            r
            for r in self.store.list_pending_for_job(job_id)
            if r.fingerprint == fingerprint
        ]
        if pending:
            approval = pending[0]
        else:
            approval = self.store.create(
                job_id=job_id,
                workspace_id=self.workspace_id,
                action=action,
                fingerprint=fingerprint,
                reason=action.reason,
                risks=list(DEFAULT_RISKS),
                checkpoint=checkpoint,
                ttl_ms=self.ttl_ms,
                originating_task_id=task_id,
            )
        raise ApprovalRequired(
            approval_id=approval.approval_id,
            job_id=job_id,
            fingerprint=fingerprint,
            checkpoint={
                **checkpoint,
                "approval_id": approval.approval_id,
                "fingerprint": fingerprint,
                "action": action.model_dump(mode="json"),
            },
        )

    def approve(
        self,
        approval_id: str,
        *,
        fingerprint: str,
        note: str | None = None,
    ) -> ApprovalRecord:
        try:
            return self.store.transition(
                approval_id,
                from_status=ApprovalStatus.PENDING,
                to_status=ApprovalStatus.APPROVED,
                decided_by=self.decided_by_label,
                decision_note=note,
                require_fingerprint=fingerprint,
            )
        except ApprovalStoreError as exc:
            message = str(exc)
            if "expired" in message.lower():
                raise ApprovalExpired(message) from exc
            if "Fingerprint" in message:
                raise FingerprintMismatch(message) from exc
            raise ApprovalConflict(message) from exc

    def reject(
        self,
        approval_id: str,
        *,
        note: str | None = None,
    ) -> ApprovalRecord:
        try:
            return self.store.transition(
                approval_id,
                from_status=ApprovalStatus.PENDING,
                to_status=ApprovalStatus.REJECTED,
                decided_by=self.decided_by_label,
                decision_note=note,
            )
        except ApprovalStoreError as exc:
            raise ApprovalConflict(str(exc)) from exc

    def revise(
        self,
        approval_id: str,
        *,
        action: ProtectedAction,
        checkpoint: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> ApprovalRecord:
        """Supersede a pending proposal; altered args need fresh approval."""

        current = self.store.get(approval_id)
        if current.status is not ApprovalStatus.PENDING:
            raise ApprovalConflict(
                f"Only pending approvals can be revised (status={current.status.value})."
            )
        if current.workspace_id != self.workspace_id:
            raise ApprovalConflict("Approval workspace mismatch.")
        self.store.transition(
            approval_id,
            from_status=ApprovalStatus.PENDING,
            to_status=ApprovalStatus.SUPERSEDED,
            decided_by=self.decided_by_label,
            decision_note="Superseded by revised proposal.",
        )
        fingerprint = ProposalFingerprint.of(
            action, job_id=current.job_id, task_id=task_id or current.originating_task_id
        )
        return self.store.create(
            job_id=current.job_id,
            workspace_id=self.workspace_id,
            action=action,
            fingerprint=fingerprint,
            reason=action.reason,
            risks=list(DEFAULT_RISKS),
            checkpoint=checkpoint or current.checkpoint,
            ttl_ms=self.ttl_ms,
            originating_task_id=task_id or current.originating_task_id,
            supersedes_id=approval_id,
        )

    def get(self, approval_id: str) -> ApprovalRecord:
        record = self.store.get(approval_id)
        if record.workspace_id != self.workspace_id:
            raise ApprovalConflict("Approval workspace mismatch.")
        return record

    def list_pending(self, *, limit: int = 50) -> list[ApprovalRecord]:
        self.store.expire_due()
        return self.store.list(
            workspace_id=self.workspace_id,
            status=ApprovalStatus.PENDING,
            limit=limit,
        )
