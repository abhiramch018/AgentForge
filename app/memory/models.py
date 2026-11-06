"""Memory record shapes.

Retrieved memories are untrusted reference data. They must never override the
current task, tool permissions, or human-approval requirements.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MemoryTrust(str, Enum):
    """How much process trust to attach — never factual proof."""

    REVIEWED = "reviewed"
    UNREVIEWED = "unreviewed"
    FAILED_APPROACH = "failed_approach"


class MemoryKind(str, Enum):
    TASK_SUMMARY = "task_summary"
    OUTCOME = "outcome"


class MemoryRecord(BaseModel):
    memory_id: str
    workspace_id: str
    kind: MemoryKind
    trust: MemoryTrust
    summary: str
    provenance: dict[str, Any] = Field(default_factory=dict)
    content_hash: str
    created_at_ms: float
    updated_at_ms: float
    deleted_at_ms: float | None = None
    score: float | None = None  # filled on retrieval only


class MemoryRetrievalMetrics(BaseModel):
    enabled: bool = False
    available: bool = True
    retrieval_ms: float | None = None
    embed_ms: float | None = None
    embed_cache_hits: int = 0
    returned_count: int = 0
    context_chars: int = 0
    written_count: int = 0
    skipped_duplicates: int = 0
    error: str | None = None
