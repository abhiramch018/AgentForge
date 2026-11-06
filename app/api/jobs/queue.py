"""Queue delivery for job ids only (Huey SqliteHuey).

The queue is not the source of truth. Authoritative status lives in JobStore.
Huey ``retries=0``: infrastructure retries are owned by JobStore lease recovery.
"""

from __future__ import annotations

from pathlib import Path

from huey import SqliteHuey


class JobQueue:
    """Thin delivery layer: messages contain only ``job_id``."""

    def __init__(self, filename: str | Path, *, immediate: bool = False) -> None:
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.huey = SqliteHuey(filename=str(path), fsync=True)
        self.huey.immediate = immediate

        @self.huey.task(retries=0)
        def process_job_message(job_id: str) -> str:
            from app.api.jobs.runtime import get_runtime

            get_runtime().handle_delivery(job_id)
            return job_id

        self._process_job_message = process_job_message

    def enqueue(self, job_id: str) -> None:
        self._process_job_message(job_id)


# Default module-level queue for ``huey_consumer`` style imports.
# Worker/API bootstrap replaces this via ``configure_default_queue``.
default_queue: JobQueue | None = None
huey = None


def configure_default_queue(
    filename: str | Path,
    *,
    immediate: bool = False,
) -> JobQueue:
    global default_queue, huey
    default_queue = JobQueue(filename, immediate=immediate)
    huey = default_queue.huey
    return default_queue


def enqueue_job(job_id: str) -> None:
    if default_queue is None:
        raise RuntimeError("Job queue is not configured.")
    default_queue.enqueue(job_id)
