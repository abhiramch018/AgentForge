"""SQLite authoritative job store.

Durability: job rows are committed before queue publish. An in-memory dict is
never treated as durable. WAL mode is enabled for local multi-reader use.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.api.jobs.models import (
    JobRecord,
    JobStatus,
    JobTiming,
    PAUSED_STATUSES,
    TERMINAL_STATUSES,
)


class JobStoreError(Exception):
    pass


class JobNotFound(JobStoreError):
    pass


class JobStore:
    """Thread-safe SQLite job persistence for one service/worker process tree."""

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
                    CREATE TABLE IF NOT EXISTS jobs (
                        job_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        request_json TEXT NOT NULL,
                        created_at_ms REAL NOT NULL,
                        updated_at_ms REAL NOT NULL,
                        attempt INTEGER NOT NULL DEFAULT 0,
                        max_attempts INTEGER NOT NULL,
                        owner_id TEXT,
                        lease_expires_at_ms REAL,
                        error_code TEXT,
                        error_message TEXT,
                        uncertainty TEXT,
                        result_json TEXT,
                        checkpoint_json TEXT,
                        trace_context_json TEXT,
                        started_at_ms REAL,
                        finished_at_ms REAL
                    )
                    """
                )
                # Migrate older DBs that lack newer columns.
                cols = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
                }
                if "checkpoint_json" not in cols:
                    conn.execute(
                        "ALTER TABLE jobs ADD COLUMN checkpoint_json TEXT"
                    )
                if "trace_context_json" not in cols:
                    conn.execute(
                        "ALTER TABLE jobs ADD COLUMN trace_context_json TEXT"
                    )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)"
                )
                conn.commit()
            finally:
                conn.close()

    def create_queued_job(
        self,
        request: dict[str, Any],
        *,
        max_attempts: int,
        job_id: str | None = None,
        trace_context: dict[str, str] | None = None,
    ) -> JobRecord:
        now = time.time() * 1000
        record_id = job_id or str(uuid.uuid4())
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO jobs (
                        job_id, status, request_json, created_at_ms, updated_at_ms,
                        attempt, max_attempts, trace_context_json
                    ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        record_id,
                        JobStatus.QUEUED.value,
                        json.dumps(request),
                        now,
                        now,
                        max_attempts,
                        json.dumps(trace_context) if trace_context else None,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return self.get(record_id)

    def get(self, job_id: str) -> JobRecord:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            raise JobNotFound(f"Unknown job_id '{job_id}'.")
        return _row_to_record(row)

    def try_claim(
        self,
        job_id: str,
        *,
        owner_id: str,
        lease_ms: float,
        now_ms: float | None = None,
        count_attempt: bool = True,
    ) -> JobRecord | None:
        """Atomically claim a queued/interrupted/expired-running job.

        Returns None if another worker owns a valid lease, the job is
        terminal, or the job is awaiting human approval. Prevents ordinary
        duplicate delivery from running the same job concurrently.

        ``count_attempt=False`` is for post-approval resume so HITL pause
        does not consume infrastructure retry budget.
        """

        now = now_ms if now_ms is not None else time.time() * 1000
        lease_expires = now + lease_ms
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    return None
                status = JobStatus(row["status"])
                if status in TERMINAL_STATUSES or status in PAUSED_STATUSES:
                    return None
                if status is JobStatus.RUNNING:
                    expires = row["lease_expires_at_ms"]
                    if expires is not None and expires > now:
                        return None
                attempt = int(row["attempt"])
                if count_attempt:
                    attempt += 1
                if attempt > int(row["max_attempts"]):
                    conn.execute(
                        """
                        UPDATE jobs SET status = ?, updated_at_ms = ?,
                        error_code = ?, error_message = ?, uncertainty = ?,
                        finished_at_ms = ?, owner_id = NULL, lease_expires_at_ms = NULL
                        WHERE job_id = ?
                        """,
                        (
                            JobStatus.FAILED.value,
                            now,
                            "retry_budget_exhausted",
                            "Job exceeded max infrastructure attempts.",
                            row["uncertainty"]
                            or "Prior attempt may have ended uncertainly.",
                            now,
                            job_id,
                        ),
                    )
                    conn.commit()
                    return None
                started = row["started_at_ms"] or now
                conn.execute(
                    """
                    UPDATE jobs SET status = ?, updated_at_ms = ?, attempt = ?,
                    owner_id = ?, lease_expires_at_ms = ?, started_at_ms = ?,
                    error_code = NULL, error_message = NULL
                    WHERE job_id = ?
                    """,
                    (
                        JobStatus.RUNNING.value,
                        now,
                        attempt,
                        owner_id,
                        lease_expires,
                        started,
                        job_id,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return self.get(job_id)

    def mark_awaiting_approval(
        self,
        job_id: str,
        *,
        owner_id: str,
        checkpoint: dict[str, Any],
        result: dict[str, Any] | None = None,
    ) -> JobRecord:
        """Pause for HITL: release lease/capacity ownership, keep durable state."""

        now = time.time() * 1000
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    UPDATE jobs SET status = ?, updated_at_ms = ?,
                    checkpoint_json = ?, result_json = COALESCE(?, result_json),
                    owner_id = NULL, lease_expires_at_ms = NULL
                    WHERE job_id = ? AND owner_id = ? AND status = ?
                    """,
                    (
                        JobStatus.AWAITING_APPROVAL.value,
                        now,
                        json.dumps(checkpoint),
                        json.dumps(result) if result is not None else None,
                        job_id,
                        owner_id,
                        JobStatus.RUNNING.value,
                    ),
                )
                if cur.rowcount != 1:
                    raise JobStoreError(
                        "Could not mark job awaiting approval (ownership lost)."
                    )
                conn.commit()
            finally:
                conn.close()
        return self.get(job_id)

    def update_checkpoint(
        self,
        job_id: str,
        *,
        checkpoint: dict[str, Any],
        expected_status: JobStatus,
    ) -> JobRecord:
        now = time.time() * 1000
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    UPDATE jobs SET checkpoint_json = ?, updated_at_ms = ?
                    WHERE job_id = ? AND status = ?
                    """,
                    (
                        json.dumps(checkpoint),
                        now,
                        job_id,
                        expected_status.value,
                    ),
                )
                if cur.rowcount != 1:
                    raise JobStoreError("Could not update checkpoint for job.")
                conn.commit()
            finally:
                conn.close()
        return self.get(job_id)

    def queue_for_resume(self, job_id: str) -> JobRecord:
        """Move an awaiting-approval job back to queued after human approve."""

        now = time.time() * 1000
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    UPDATE jobs SET status = ?, updated_at_ms = ?,
                    owner_id = NULL, lease_expires_at_ms = NULL,
                    error_code = NULL, error_message = NULL
                    WHERE job_id = ? AND status = ?
                    """,
                    (
                        JobStatus.QUEUED.value,
                        now,
                        job_id,
                        JobStatus.AWAITING_APPROVAL.value,
                    ),
                )
                if cur.rowcount != 1:
                    # Idempotent if already queued/running/terminal after resume.
                    row = conn.execute(
                        "SELECT status FROM jobs WHERE job_id = ?",
                        (job_id,),
                    ).fetchone()
                    if row is None:
                        raise JobNotFound(f"Unknown job_id '{job_id}'.")
                    status = JobStatus(row["status"])
                    if status in {
                        JobStatus.QUEUED,
                        JobStatus.RUNNING,
                        JobStatus.SUCCEEDED,
                        JobStatus.INTERRUPTED,
                    }:
                        conn.commit()
                        return self.get(job_id)
                    raise JobStoreError(
                        f"Job cannot resume from status {status.value}."
                    )
                conn.commit()
            finally:
                conn.close()
        return self.get(job_id)

    def mark_failed_from_any(
        self,
        job_id: str,
        *,
        error_code: str,
        error_message: str,
    ) -> JobRecord:
        """Fail a non-terminal job (e.g. approval rejected)."""

        now = time.time() * 1000
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    UPDATE jobs SET status = ?, updated_at_ms = ?, finished_at_ms = ?,
                    error_code = ?, error_message = ?,
                    owner_id = NULL, lease_expires_at_ms = NULL
                    WHERE job_id = ? AND status NOT IN (?, ?)
                    """,
                    (
                        JobStatus.FAILED.value,
                        now,
                        now,
                        error_code,
                        error_message,
                        job_id,
                        JobStatus.SUCCEEDED.value,
                        JobStatus.FAILED.value,
                    ),
                )
                if cur.rowcount != 1:
                    raise JobStoreError("Could not fail job from current status.")
                conn.commit()
            finally:
                conn.close()
        return self.get(job_id)

    def heartbeat(self, job_id: str, *, owner_id: str, lease_ms: float) -> bool:
        now = time.time() * 1000
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    UPDATE jobs SET lease_expires_at_ms = ?, updated_at_ms = ?
                    WHERE job_id = ? AND owner_id = ? AND status = ?
                    """,
                    (now + lease_ms, now, job_id, owner_id, JobStatus.RUNNING.value),
                )
                conn.commit()
                return cur.rowcount == 1
            finally:
                conn.close()

    def mark_succeeded(
        self,
        job_id: str,
        *,
        owner_id: str,
        result: dict[str, Any],
    ) -> JobRecord:
        now = time.time() * 1000
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    UPDATE jobs SET status = ?, updated_at_ms = ?, finished_at_ms = ?,
                    result_json = ?, owner_id = NULL, lease_expires_at_ms = NULL,
                    error_code = NULL, error_message = NULL, uncertainty = NULL
                    WHERE job_id = ? AND owner_id = ? AND status = ?
                    """,
                    (
                        JobStatus.SUCCEEDED.value,
                        now,
                        now,
                        json.dumps(result),
                        job_id,
                        owner_id,
                        JobStatus.RUNNING.value,
                    ),
                )
                if cur.rowcount != 1:
                    raise JobStoreError("Could not mark job succeeded (ownership lost).")
                conn.commit()
            finally:
                conn.close()
        return self.get(job_id)

    def release_to_interrupted(
        self,
        job_id: str,
        *,
        owner_id: str,
        uncertainty: str,
    ) -> JobRecord:
        """Return a claimed job to interrupted so it can be retried later."""

        now = time.time() * 1000
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    UPDATE jobs SET status = ?, updated_at_ms = ?,
                    uncertainty = ?, owner_id = NULL, lease_expires_at_ms = NULL
                    WHERE job_id = ? AND owner_id = ? AND status = ?
                    """,
                    (
                        JobStatus.INTERRUPTED.value,
                        now,
                        uncertainty,
                        job_id,
                        owner_id,
                        JobStatus.RUNNING.value,
                    ),
                )
                if cur.rowcount != 1:
                    raise JobStoreError("Could not release job claim.")
                conn.commit()
            finally:
                conn.close()
        return self.get(job_id)

    def mark_failed(
        self,
        job_id: str,
        *,
        owner_id: str,
        error_code: str,
        error_message: str,
        uncertainty: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> JobRecord:
        now = time.time() * 1000
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    UPDATE jobs SET status = ?, updated_at_ms = ?, finished_at_ms = ?,
                    error_code = ?, error_message = ?, uncertainty = ?,
                    result_json = ?, owner_id = NULL, lease_expires_at_ms = NULL
                    WHERE job_id = ? AND owner_id = ? AND status = ?
                    """,
                    (
                        JobStatus.FAILED.value,
                        now,
                        now,
                        error_code,
                        error_message,
                        uncertainty,
                        json.dumps(result) if result is not None else None,
                        job_id,
                        owner_id,
                        JobStatus.RUNNING.value,
                    ),
                )
                if cur.rowcount != 1:
                    raise JobStoreError("Could not mark job failed (ownership lost).")
                conn.commit()
            finally:
                conn.close()
        return self.get(job_id)

    def recover_expired_leases(
        self,
        *,
        now_ms: float | None = None,
    ) -> list[str]:
        """Mark expired running jobs interrupted and return ids to re-enqueue."""

        now = now_ms if now_ms is not None else time.time() * 1000
        requeue: list[str] = []
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT job_id, attempt, max_attempts, uncertainty FROM jobs
                    WHERE status = ? AND lease_expires_at_ms IS NOT NULL
                      AND lease_expires_at_ms < ?
                    """,
                    (JobStatus.RUNNING.value, now),
                ).fetchall()
                for row in rows:
                    job_id = row["job_id"]
                    uncertainty = (
                        row["uncertainty"]
                        or "Worker lease expired; external LLM/tool outcome may be uncertain."
                    )
                    if int(row["attempt"]) >= int(row["max_attempts"]):
                        conn.execute(
                            """
                            UPDATE jobs SET status = ?, updated_at_ms = ?,
                            finished_at_ms = ?, error_code = ?, error_message = ?,
                            uncertainty = ?, owner_id = NULL, lease_expires_at_ms = NULL
                            WHERE job_id = ? AND status = ?
                            """,
                            (
                                JobStatus.FAILED.value,
                                now,
                                now,
                                "retry_budget_exhausted",
                                "Lease expired and retry budget was exhausted.",
                                uncertainty,
                                job_id,
                                JobStatus.RUNNING.value,
                            ),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE jobs SET status = ?, updated_at_ms = ?,
                            uncertainty = ?, owner_id = NULL, lease_expires_at_ms = NULL
                            WHERE job_id = ? AND status = ?
                            """,
                            (
                                JobStatus.INTERRUPTED.value,
                                now,
                                uncertainty,
                                job_id,
                                JobStatus.RUNNING.value,
                            ),
                        )
                        requeue.append(job_id)
                conn.commit()
            finally:
                conn.close()
        return requeue

    def list_needs_delivery(self) -> list[str]:
        """Queued/interrupted jobs that should have a queue message."""

        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT job_id FROM jobs
                    WHERE status IN (?, ?)
                    ORDER BY created_at_ms ASC
                    """,
                    (JobStatus.QUEUED.value, JobStatus.INTERRUPTED.value),
                ).fetchall()
            finally:
                conn.close()
        return [row["job_id"] for row in rows]

    def list_recent(self, *, limit: int = 50, offset: int = 0) -> list[JobRecord]:
        """Newest-first job list for dashboard overview (no fabricated rows)."""

        if limit < 1 or limit > 200:
            raise ValueError("limit must be 1..200")
        if offset < 0:
            raise ValueError("offset must be >= 0")
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM jobs
                    ORDER BY created_at_ms DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()
            finally:
                conn.close()
        return [_row_to_record(row) for row in rows]

    def status_counts(self) -> dict[str, int]:
        """Authoritative per-status counts from the durable job store."""

        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT status, COUNT(*) AS n FROM jobs
                    GROUP BY status
                    """
                ).fetchall()
            finally:
                conn.close()
        counts = {status.value: 0 for status in JobStatus}
        for row in rows:
            key = str(row["status"])
            if key in counts:
                counts[key] = int(row["n"])
        return counts


def _row_to_record(row: sqlite3.Row) -> JobRecord:
    created = float(row["created_at_ms"])
    started = row["started_at_ms"]
    finished = row["finished_at_ms"]
    queue_wait = None
    execute_ms = None
    total_ms = None
    if started is not None:
        queue_wait = max(0.0, float(started) - created)
    if started is not None and finished is not None:
        execute_ms = max(0.0, float(finished) - float(started))
    if finished is not None:
        total_ms = max(0.0, float(finished) - created)
    result = None
    if row["result_json"]:
        result = json.loads(row["result_json"])
    checkpoint = None
    # Older rows / PRAGMA-added columns may be missing from Row keys in theory;
    # sqlite3.Row supports name access and returns None for NULL.
    try:
        raw_checkpoint = row["checkpoint_json"]
    except (IndexError, KeyError):
        raw_checkpoint = None
    if raw_checkpoint:
        checkpoint = json.loads(raw_checkpoint)
    trace_context = None
    try:
        raw_trace = row["trace_context_json"]
    except (IndexError, KeyError):
        raw_trace = None
    if raw_trace:
        trace_context = json.loads(raw_trace)
    return JobRecord(
        job_id=row["job_id"],
        status=JobStatus(row["status"]),
        request=json.loads(row["request_json"]),
        created_at_ms=created,
        updated_at_ms=float(row["updated_at_ms"]),
        attempt=int(row["attempt"]),
        max_attempts=int(row["max_attempts"]),
        owner_id=row["owner_id"],
        lease_expires_at_ms=row["lease_expires_at_ms"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        uncertainty=row["uncertainty"],
        result=result,
        checkpoint=checkpoint,
        trace_context=trace_context,
        timing=JobTiming(
            submitted_at_ms=created,
            started_at_ms=float(started) if started is not None else None,
            finished_at_ms=float(finished) if finished is not None else None,
            queue_wait_ms=queue_wait,
            execute_ms=execute_ms,
            total_ms=total_ms,
        ),
    )
