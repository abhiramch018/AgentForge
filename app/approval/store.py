"""SQLite approval proposals (same durability model as M8 JobStore)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.approval.models import ApprovalRecord, ApprovalStatus, ProtectedAction


class ApprovalStoreError(Exception):
    pass


class ApprovalNotFound(ApprovalStoreError):
    pass


class ApprovalStore:
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
                    CREATE TABLE IF NOT EXISTS approvals (
                        approval_id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL,
                        workspace_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        action_json TEXT NOT NULL,
                        fingerprint TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        risks_json TEXT NOT NULL,
                        originating_task_id TEXT,
                        created_at_ms REAL NOT NULL,
                        updated_at_ms REAL NOT NULL,
                        expires_at_ms REAL NOT NULL,
                        decided_at_ms REAL,
                        decided_by TEXT,
                        decision_note TEXT,
                        supersedes_id TEXT,
                        checkpoint_json TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_appr_status ON approvals(status)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_appr_job ON approvals(job_id)"
                )
                conn.commit()
            finally:
                conn.close()

    def create(
        self,
        *,
        job_id: str,
        workspace_id: str,
        action: ProtectedAction,
        fingerprint: str,
        reason: str,
        risks: list[str],
        checkpoint: dict[str, Any],
        ttl_ms: float,
        originating_task_id: str | None = None,
        supersedes_id: str | None = None,
        approval_id: str | None = None,
    ) -> ApprovalRecord:
        now = time.time() * 1000
        record_id = approval_id or str(uuid.uuid4())
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO approvals (
                        approval_id, job_id, workspace_id, status, action_json,
                        fingerprint, reason, risks_json, originating_task_id,
                        created_at_ms, updated_at_ms, expires_at_ms,
                        supersedes_id, checkpoint_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record_id,
                        job_id,
                        workspace_id,
                        ApprovalStatus.PENDING.value,
                        action.model_dump_json(),
                        fingerprint,
                        reason,
                        json.dumps(risks),
                        originating_task_id,
                        now,
                        now,
                        now + ttl_ms,
                        supersedes_id,
                        json.dumps(checkpoint),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return self.get(record_id)

    def get(self, approval_id: str) -> ApprovalRecord:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM approvals WHERE approval_id = ?",
                    (approval_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            raise ApprovalNotFound(f"Unknown approval_id '{approval_id}'.")
        return _row_to_record(row)

    def list(
        self,
        *,
        workspace_id: str,
        status: ApprovalStatus | None = None,
        limit: int = 50,
    ) -> list[ApprovalRecord]:
        with self._lock:
            conn = self._connect()
            try:
                if status is None:
                    rows = conn.execute(
                        """
                        SELECT * FROM approvals
                        WHERE workspace_id = ?
                        ORDER BY created_at_ms DESC
                        LIMIT ?
                        """,
                        (workspace_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM approvals
                        WHERE workspace_id = ? AND status = ?
                        ORDER BY created_at_ms DESC
                        LIMIT ?
                        """,
                        (workspace_id, status.value, limit),
                    ).fetchall()
            finally:
                conn.close()
        return [_row_to_record(row) for row in rows]

    def list_pending_for_job(self, job_id: str) -> list[ApprovalRecord]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM approvals
                    WHERE job_id = ? AND status = ?
                    ORDER BY created_at_ms DESC
                    """,
                    (job_id, ApprovalStatus.PENDING.value),
                ).fetchall()
            finally:
                conn.close()
        return [_row_to_record(row) for row in rows]

    def transition(
        self,
        approval_id: str,
        *,
        from_status: ApprovalStatus,
        to_status: ApprovalStatus,
        decided_by: str | None = None,
        decision_note: str | None = None,
        require_fingerprint: str | None = None,
    ) -> ApprovalRecord:
        now = time.time() * 1000
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM approvals WHERE approval_id = ?",
                    (approval_id,),
                ).fetchone()
                if row is None:
                    raise ApprovalNotFound(f"Unknown approval_id '{approval_id}'.")
                if row["status"] != from_status.value:
                    raise ApprovalStoreError(
                        f"Approval is {row['status']}, expected {from_status.value}."
                    )
                if (
                    require_fingerprint is not None
                    and row["fingerprint"] != require_fingerprint
                ):
                    raise ApprovalStoreError("Fingerprint mismatch.")
                if to_status is ApprovalStatus.APPROVED and now > float(
                    row["expires_at_ms"]
                ):
                    conn.execute(
                        """
                        UPDATE approvals SET status = ?, updated_at_ms = ?,
                        decided_at_ms = ?, decided_by = ?, decision_note = ?
                        WHERE approval_id = ? AND status = ?
                        """,
                        (
                            ApprovalStatus.EXPIRED.value,
                            now,
                            now,
                            decided_by,
                            "Expired before approval.",
                            approval_id,
                            from_status.value,
                        ),
                    )
                    conn.commit()
                    raise ApprovalStoreError("Approval proposal has expired.")
                conn.execute(
                    """
                    UPDATE approvals SET status = ?, updated_at_ms = ?,
                    decided_at_ms = ?, decided_by = ?, decision_note = ?
                    WHERE approval_id = ? AND status = ?
                    """,
                    (
                        to_status.value,
                        now,
                        now,
                        decided_by,
                        decision_note,
                        approval_id,
                        from_status.value,
                    ),
                )
                if conn.total_changes < 1:
                    raise ApprovalStoreError("Concurrent approval transition lost.")
                conn.commit()
            finally:
                conn.close()
        return self.get(approval_id)

    def expire_due(self, *, now_ms: float | None = None) -> list[str]:
        now = now_ms if now_ms is not None else time.time() * 1000
        expired_ids: list[str] = []
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT approval_id FROM approvals
                    WHERE status = ? AND expires_at_ms < ?
                    """,
                    (ApprovalStatus.PENDING.value, now),
                ).fetchall()
                for row in rows:
                    aid = row["approval_id"]
                    conn.execute(
                        """
                        UPDATE approvals SET status = ?, updated_at_ms = ?,
                        decided_at_ms = ?, decision_note = ?
                        WHERE approval_id = ? AND status = ?
                        """,
                        (
                            ApprovalStatus.EXPIRED.value,
                            now,
                            now,
                            "TTL elapsed.",
                            aid,
                            ApprovalStatus.PENDING.value,
                        ),
                    )
                    expired_ids.append(aid)
                conn.commit()
            finally:
                conn.close()
        return expired_ids


def _row_to_record(row: sqlite3.Row) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=row["approval_id"],
        job_id=row["job_id"],
        workspace_id=row["workspace_id"],
        status=ApprovalStatus(row["status"]),
        action=ProtectedAction.model_validate_json(row["action_json"]),
        fingerprint=row["fingerprint"],
        reason=row["reason"],
        risks=json.loads(row["risks_json"]),
        originating_task_id=row["originating_task_id"],
        created_at_ms=float(row["created_at_ms"]),
        updated_at_ms=float(row["updated_at_ms"]),
        expires_at_ms=float(row["expires_at_ms"]),
        decided_at_ms=row["decided_at_ms"],
        decided_by=row["decided_by"],
        decision_note=row["decision_note"],
        supersedes_id=row["supersedes_id"],
        checkpoint=json.loads(row["checkpoint_json"]),
    )
