"""Readiness probes that check storage usability, not mere process liveness."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def _sqlite_readable(path: str | Path) -> bool:
    p = Path(path)
    if not p.parent.exists():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
    try:
        conn = sqlite3.connect(str(p), timeout=5, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("SELECT 1").fetchone()
            return True
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def check_api_readiness(
    *,
    jobs_db_path: str,
    queue_db_path: str,
    memory_db_path: str,
    approvals_db_path: str,
    events_db_path: str,
    artifact_root: str,
    job_store: Any | None = None,
) -> dict[str, bool]:
    """Return named boolean checks for API readiness.

    ``job_store`` when provided also exercises the authoritative job API
    (status_counts) so schema/migration failures surface as not-ready.
    """

    checks: dict[str, bool] = {
        "jobs_db": _sqlite_readable(jobs_db_path),
        "queue_db": _sqlite_readable(queue_db_path),
        "memory_db": _sqlite_readable(memory_db_path),
        "approvals_db": _sqlite_readable(approvals_db_path),
        "events_db": _sqlite_readable(events_db_path),
        "artifact_root": False,
        "job_store": False,
    }
    try:
        Path(artifact_root).mkdir(parents=True, exist_ok=True)
        probe = Path(artifact_root) / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks["artifact_root"] = True
    except OSError:
        checks["artifact_root"] = False

    if job_store is not None:
        try:
            job_store.status_counts()
            checks["job_store"] = True
        except Exception:
            checks["job_store"] = False
    else:
        checks["job_store"] = checks["jobs_db"]

    return checks


def check_worker_readiness(
    *,
    jobs_db_path: str,
    queue_db_path: str,
    job_store: Any | None = None,
    consumer_started: bool = False,
) -> dict[str, bool]:
    """Worker readiness: stores usable and consumer loop has started."""

    checks: dict[str, bool] = {
        "jobs_db": _sqlite_readable(jobs_db_path),
        "queue_db": _sqlite_readable(queue_db_path),
        "job_store": False,
        "consumer_started": consumer_started,
    }
    if job_store is not None:
        try:
            job_store.status_counts()
            checks["job_store"] = True
        except Exception:
            checks["job_store"] = False
    else:
        checks["job_store"] = checks["jobs_db"]
    return checks


def all_ready(checks: dict[str, bool]) -> bool:
    return bool(checks) and all(checks.values())
