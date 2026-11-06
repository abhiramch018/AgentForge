"""Durable job progress events (M11).

Events live in SQLite beside job state so the API process can stream what the
worker commits. Lifecycle events are not model-token streams — fabricating
token chunks from a finished reply is forbidden.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class JobEventType(str, Enum):
    JOB_SUBMITTED = "job.submitted"
    JOB_RUNNING = "job.running"
    PLANNING_STARTED = "planning.started"
    PLANNING_COMPLETED = "planning.completed"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    REVIEW_DECISION = "review.decision"
    REVISION_STARTED = "revision.started"
    APPROVAL_WAITING = "approval.waiting"
    APPROVAL_RESUMED = "approval.resumed"
    JOB_SUCCEEDED = "job.succeeded"
    JOB_FAILED = "job.failed"
    REPLAY_GAP = "stream.replay_gap"


class JobEvent(BaseModel):
    event_id: int
    job_id: str
    event_type: JobEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at_ms: float
    # Explicit: these are lifecycle events, never fabricated token deltas.
    stream_kind: str = "lifecycle"


class JobEventStore:
    """SQLite append-only event log with per-job retention."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_events_per_job: int = 500,
    ) -> None:
        self.path = str(path)
        self.max_events_per_job = max_events_per_job
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
                    CREATE TABLE IF NOT EXISTS job_events (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at_ms REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_job_events_job "
                    "ON job_events(job_id, event_id)"
                )
                conn.commit()
            finally:
                conn.close()

    def append(
        self,
        job_id: str,
        event_type: JobEventType | str,
        payload: dict[str, Any] | None = None,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> JobEvent:
        """Append one event. Optional ``conn`` shares a caller transaction."""

        et = (
            event_type
            if isinstance(event_type, JobEventType)
            else JobEventType(event_type)
        )
        now = time.time() * 1000
        owns = conn is None
        if owns:
            self._lock.acquire()
            conn = self._connect()
        assert conn is not None
        try:
            cur = conn.execute(
                """
                INSERT INTO job_events (job_id, event_type, payload_json, created_at_ms)
                VALUES (?, ?, ?, ?)
                """,
                (job_id, et.value, json.dumps(payload or {}), now),
            )
            event_id = int(cur.lastrowid)
            self._trim(conn, job_id)
            if owns:
                conn.commit()
            return JobEvent(
                event_id=event_id,
                job_id=job_id,
                event_type=et,
                payload=payload or {},
                created_at_ms=now,
            )
        finally:
            if owns:
                conn.close()
                self._lock.release()

    def _trim(self, conn: sqlite3.Connection, job_id: str) -> None:
        conn.execute(
            """
            DELETE FROM job_events
            WHERE job_id = ? AND event_id NOT IN (
                SELECT event_id FROM job_events
                WHERE job_id = ?
                ORDER BY event_id DESC
                LIMIT ?
            )
            """,
            (job_id, job_id, self.max_events_per_job),
        )

    def list_since(
        self,
        job_id: str,
        *,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> list[JobEvent]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM job_events
                    WHERE job_id = ? AND event_id > ?
                    ORDER BY event_id ASC
                    LIMIT ?
                    """,
                    (job_id, after_event_id, limit),
                ).fetchall()
            finally:
                conn.close()
        return [_row_to_event(row) for row in rows]

    def earliest_id(self, job_id: str) -> int | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT MIN(event_id) AS m FROM job_events WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None or row["m"] is None:
            return None
        return int(row["m"])

    def latest_id(self, job_id: str) -> int | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT MAX(event_id) AS m FROM job_events WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None or row["m"] is None:
            return None
        return int(row["m"])


class NullEventStore:
    """No-op emitter when streaming is unavailable."""

    def append(
        self,
        job_id: str,
        event_type: JobEventType | str,
        payload: dict[str, Any] | None = None,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        return None

    def list_since(
        self,
        job_id: str,
        *,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> list[JobEvent]:
        return []

    def earliest_id(self, job_id: str) -> int | None:
        return None

    def latest_id(self, job_id: str) -> int | None:
        return None


def _row_to_event(row: sqlite3.Row) -> JobEvent:
    return JobEvent(
        event_id=int(row["event_id"]),
        job_id=row["job_id"],
        event_type=JobEventType(row["event_type"]),
        payload=json.loads(row["payload_json"]),
        created_at_ms=float(row["created_at_ms"]),
    )
