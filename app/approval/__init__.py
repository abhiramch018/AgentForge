"""Human-in-the-loop approval (M10). Separate from M6 reviewer acceptance."""

from app.approval.actions import ArtifactWriteResult, execute_write_artifact
from app.approval.exceptions import ApprovalRequired, ApprovalError
from app.approval.models import (
    ApprovalDecision,
    ApprovalRecord,
    ApprovalStatus,
    ProtectedAction,
    ProposalFingerprint,
)
from app.approval.service import ApprovalService
from app.approval.store import ApprovalNotFound, ApprovalStore

__all__ = [
    "ApprovalDecision",
    "ApprovalError",
    "ApprovalNotFound",
    "ApprovalRecord",
    "ApprovalRequired",
    "ApprovalService",
    "ApprovalStatus",
    "ApprovalStore",
    "ArtifactWriteResult",
    "ProtectedAction",
    "ProposalFingerprint",
    "execute_write_artifact",
]
