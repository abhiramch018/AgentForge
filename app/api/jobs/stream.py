"""SSE generators for durable job events (cross-process via SQLite)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi.sse import ServerSentEvent

from app.api.jobs.events import JobEvent, JobEventStore
from app.api.jobs.models import TERMINAL_STATUSES
from app.api.jobs.store import JobNotFound, JobStore


async def job_event_sse(
    *,
    job_id: str,
    event_store: JobEventStore,
    job_store: JobStore,
    last_event_id: int = 0,
    poll_interval_ms: float = 100.0,
    heartbeat_seconds: float = 15.0,
    buffer_limit: int = 50,
) -> AsyncIterator[ServerSentEvent]:
    """Yield committed lifecycle events; never cancel the underlying job.

    Reconnection: clients send ``Last-Event-ID``. If that id is older than
    retained history, a ``stream.replay_gap`` notice is sent (not a token
    stream) and replay continues from the earliest retained event.

    Slow clients: each poll returns at most ``buffer_limit`` events; the
    client continues via ``Last-Event-ID``. Disconnect does not release
    worker capacity or cancel the job.
    """

    try:
        job_store.get(job_id)
    except JobNotFound:
        yield ServerSentEvent(
            data={
                "error": "job_not_found",
                "message": f"Unknown job_id '{job_id}'.",
                "stream_kind": "lifecycle",
            },
            event="error",
            id="0",
        )
        return

    cursor = last_event_id
    if last_event_id > 0:
        earliest = event_store.earliest_id(job_id)
        if earliest is not None and last_event_id < earliest - 1:
            yield ServerSentEvent(
                data={
                    "event_type": "stream.replay_gap",
                    "payload": {
                        "requested_after": last_event_id,
                        "earliest_retained": earliest,
                        "message": (
                            "Requested history expired under retention; "
                            "replaying from earliest retained event."
                        ),
                    },
                    "stream_kind": "lifecycle",
                },
                event="stream.replay_gap",
                id=str(earliest - 1),
            )
            cursor = earliest - 1

    last_heartbeat = time.monotonic()
    idle_rounds = 0
    while True:
        batch = event_store.list_since(
            job_id, after_event_id=cursor, limit=buffer_limit
        )
        if batch:
            idle_rounds = 0
            for event in batch:
                yield _to_sse(event)
                cursor = event.event_id
        else:
            idle_rounds += 1
            try:
                record = job_store.get(job_id)
            except JobNotFound:
                break
            if record.status in TERMINAL_STATUSES:
                trailing = event_store.list_since(
                    job_id, after_event_id=cursor, limit=buffer_limit
                )
                for event in trailing:
                    yield _to_sse(event)
                    cursor = event.event_id
                if not trailing or idle_rounds > 2:
                    break

        now = time.monotonic()
        if now - last_heartbeat >= heartbeat_seconds:
            yield ServerSentEvent(comment=f"heartbeat {int(now)}")
            last_heartbeat = now

        await asyncio.sleep(poll_interval_ms / 1000.0)


def _to_sse(event: JobEvent) -> ServerSentEvent:
    payload: dict[str, Any] = {
        "event_id": event.event_id,
        "job_id": event.job_id,
        "event_type": event.event_type.value,
        "payload": event.payload,
        "created_at_ms": event.created_at_ms,
        "stream_kind": event.stream_kind,
    }
    return ServerSentEvent(
        data=payload,
        event=event.event_type.value,
        id=str(event.event_id),
    )
