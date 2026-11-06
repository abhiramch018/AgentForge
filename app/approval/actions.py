"""Trusted execution of the narrowly scoped artifact write action."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, Field

from app.approval.exceptions import ApprovalError
from app.approval.models import ProtectedAction


class ArtifactWriteResult(BaseModel):
    path: str
    bytes_written: int = Field(ge=0)
    content_sha256: str
    idempotent_replay: bool = False


def execute_write_artifact(
    action: ProtectedAction,
    *,
    output_root: str | Path,
) -> ArtifactWriteResult:
    """Write ``action.content`` under ``output_root`` after path validation.

    Idempotent for identical content at the same path: rewriting the same
    bytes is a no-op success. Different content at an existing path raises.
    """

    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / action.relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ApprovalError("Artifact path escapes the configured output root.") from exc

    digest = hashlib.sha256(action.content.encode("utf-8")).hexdigest()
    if target.exists():
        existing = target.read_bytes()
        if existing == action.content.encode("utf-8"):
            return ArtifactWriteResult(
                path=str(target),
                bytes_written=len(existing),
                content_sha256=digest,
                idempotent_replay=True,
            )
        raise ApprovalError(
            "Artifact already exists with different content; refusing overwrite."
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    data = action.content.encode("utf-8")
    target.write_bytes(data)
    return ArtifactWriteResult(
        path=str(target),
        bytes_written=len(data),
        content_sha256=digest,
        idempotent_replay=False,
    )
