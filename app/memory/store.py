"""SQLite durable memory store (long-term).

Extends the M8 SQLite approach: authoritative rows in SQLite, embeddings
stored as JSON floats beside the text. No separate vector database — keeps
the local Windows stack small. Soft-delete invalidates rows for retrieval;
vectors are removed on hard purge after retention.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.memory.models import MemoryKind, MemoryRecord, MemoryTrust


class MemoryStoreError(Exception):
    pass


class MemoryNotFound(MemoryStoreError):
    pass


class MemoryStore:
    """Thread-safe SQLite persistence for one workspace deployment."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memories (
                        memory_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        trust TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        provenance_json TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        embedding_json TEXT NOT NULL,
                        created_at_ms REAL NOT NULL,
                        updated_at_ms REAL NOT NULL,
                        deleted_at_ms REAL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_mem_ws_hash
                    ON memories(workspace_id, content_hash)
                    WHERE deleted_at_ms IS NULL
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_mem_ws_active
                    ON memories(workspace_id, deleted_at_ms, created_at_ms)
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def insert(
        self,
        *,
        workspace_id: str,
        kind: MemoryKind,
        trust: MemoryTrust,
        summary: str,
        provenance: dict[str, Any],
        content_hash: str,
        embedding: list[float],
        memory_id: str | None = None,
    ) -> tuple[MemoryRecord | None, bool]:
        """Insert a memory. Returns ``(record, created)``.

        If an active row with the same workspace+content_hash exists, returns
        ``(existing, False)`` without rewriting (duplicate prevention).
        """

        now = time.time() * 1000
        record_id = memory_id or str(uuid.uuid4())
        with self._lock:
            conn = self._connect()
            try:
                existing = conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE workspace_id = ? AND content_hash = ?
                      AND deleted_at_ms IS NULL
                    """,
                    (workspace_id, content_hash),
                ).fetchone()
                if existing is not None:
                    return _row_to_record(existing), False
                conn.execute(
                    """
                    INSERT INTO memories (
                        memory_id, workspace_id, kind, trust, summary,
                        provenance_json, content_hash, embedding_json,
                        created_at_ms, updated_at_ms, deleted_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        record_id,
                        workspace_id,
                        kind.value,
                        trust.value,
                        summary,
                        json.dumps(provenance),
                        content_hash,
                        json.dumps(embedding),
                        now,
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return self.get(record_id, workspace_id=workspace_id), True

    def get(self, memory_id: str, *, workspace_id: str) -> MemoryRecord:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE memory_id = ? AND workspace_id = ?
                    """,
                    (memory_id, workspace_id),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            raise MemoryNotFound(f"Unknown memory_id '{memory_id}'.")
        return _row_to_record(row)

    def list_active(
        self,
        *,
        workspace_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE workspace_id = ? AND deleted_at_ms IS NULL
                    ORDER BY created_at_ms DESC
                    LIMIT ? OFFSET ?
                    """,
                    (workspace_id, limit, offset),
                ).fetchall()
            finally:
                conn.close()
        return [_row_to_record(row) for row in rows]

    def list_active_with_embeddings(
        self,
        *,
        workspace_id: str,
        limit: int = 500,
    ) -> list[tuple[MemoryRecord, list[float]]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE workspace_id = ? AND deleted_at_ms IS NULL
                    ORDER BY created_at_ms DESC
                    LIMIT ?
                    """,
                    (workspace_id, limit),
                ).fetchall()
            finally:
                conn.close()
        out: list[tuple[MemoryRecord, list[float]]] = []
        for row in rows:
            emb = json.loads(row["embedding_json"])
            out.append((_row_to_record(row), emb))
        return out

    def soft_delete(self, memory_id: str, *, workspace_id: str) -> MemoryRecord:
        """Invalidate for retrieval and clear the embedding vector."""

        now = time.time() * 1000
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    UPDATE memories
                    SET deleted_at_ms = ?, updated_at_ms = ?, embedding_json = '[]'
                    WHERE memory_id = ? AND workspace_id = ?
                      AND deleted_at_ms IS NULL
                    """,
                    (now, now, memory_id, workspace_id),
                )
                if cur.rowcount != 1:
                    raise MemoryNotFound(f"Unknown memory_id '{memory_id}'.")
                conn.commit()
            finally:
                conn.close()
        return self.get(memory_id, workspace_id=workspace_id)

    def soft_delete_all(self, *, workspace_id: str) -> int:
        now = time.time() * 1000
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    UPDATE memories
                    SET deleted_at_ms = ?, updated_at_ms = ?, embedding_json = '[]'
                    WHERE workspace_id = ? AND deleted_at_ms IS NULL
                    """,
                    (now, now, workspace_id),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def purge_deleted_older_than(self, *, older_than_ms: float) -> int:
        """Hard-delete soft-deleted rows past retention (and orphaned vectors)."""

        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    DELETE FROM memories
                    WHERE deleted_at_ms IS NOT NULL AND deleted_at_ms < ?
                    """,
                    (older_than_ms,),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def purge_older_than(
        self,
        *,
        workspace_id: str,
        created_before_ms: float,
    ) -> int:
        """Soft-delete active memories older than retention for a workspace."""

        now = time.time() * 1000
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    UPDATE memories
                    SET deleted_at_ms = ?, updated_at_ms = ?, embedding_json = '[]'
                    WHERE workspace_id = ? AND deleted_at_ms IS NULL
                      AND created_at_ms < ?
                    """,
                    (now, now, workspace_id, created_before_ms),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        memory_id=row["memory_id"],
        workspace_id=row["workspace_id"],
        kind=MemoryKind(row["kind"]),
        trust=MemoryTrust(row["trust"]),
        summary=row["summary"],
        provenance=json.loads(row["provenance_json"]),
        content_hash=row["content_hash"],
        created_at_ms=float(row["created_at_ms"]),
        updated_at_ms=float(row["updated_at_ms"]),
        deleted_at_ms=(
            float(row["deleted_at_ms"]) if row["deleted_at_ms"] is not None else None
        ),
    )
