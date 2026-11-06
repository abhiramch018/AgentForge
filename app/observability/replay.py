"""Safe timeline replay from durable M11 events — no model/tool calls.

This is **timeline playback**, not workflow re-execution. It never calls
models, runs tools, or reuses prior human approvals. A true rerun must
submit a new job under current permissions and approval rules.

Deterministic offline “response capture” re-execution is out of scope here:
M11 events carry operational lifecycle markers, not full LLM transcripts.
Missing event history (retention cap) yields a partial timeline.
"""

from __future__ import annotations

from app.api.jobs.events import JobEvent, JobEventStore, JobEventType
from app.observability.models import LatencyBreakdown, ReplayEvent, ReplayTimeline
from app.observability.redaction import redact_mapping


def build_replay_timeline(
    job_id: str,
    event_store: JobEventStore,
    *,
    events: list[JobEvent] | None = None,
    capture_content: bool = False,
) -> ReplayTimeline:
    rows = events if events is not None else event_store.list_since(
        job_id, after_event_id=0, limit=10_000
    )
    replay_events: list[ReplayEvent] = []
    prev_ts: float | None = None
    for row in rows:
        delta = None if prev_ts is None else max(0.0, row.created_at_ms - prev_ts)
        replay_events.append(
            ReplayEvent(
                event_id=row.event_id,
                event_type=row.event_type.value,
                created_at_ms=row.created_at_ms,
                delta_ms=delta,
                payload=redact_mapping(
                    dict(row.payload), capture_content=capture_content
                ),
            )
        )
        prev_ts = row.created_at_ms

    return ReplayTimeline(
        job_id=job_id,
        event_count=len(replay_events),
        events=replay_events,
        latency=_latency_from_events(rows),
    )


def _latency_from_events(rows: list[JobEvent]) -> LatencyBreakdown:
    by_type = {e.event_type: e for e in rows}
    # Prefer first/last timestamps for phases.
    submitted = _first(rows, JobEventType.JOB_SUBMITTED)
    running = _first(rows, JobEventType.JOB_RUNNING)
    plan_start = _first(rows, JobEventType.PLANNING_STARTED)
    plan_done = _first(rows, JobEventType.PLANNING_COMPLETED)
    approval_wait = _first(rows, JobEventType.APPROVAL_WAITING)
    approval_resume = _first(rows, JobEventType.APPROVAL_RESUMED)
    terminal = _first(
        rows, JobEventType.JOB_SUCCEEDED
    ) or _first(rows, JobEventType.JOB_FAILED)

    queue_wait = None
    if submitted and running:
        queue_wait = max(0.0, running.created_at_ms - submitted.created_at_ms)
    planning_ms = None
    if plan_start and plan_done:
        planning_ms = max(0.0, plan_done.created_at_ms - plan_start.created_at_ms)
    approval_wait_ms = None
    if approval_wait and approval_resume:
        approval_wait_ms = max(
            0.0, approval_resume.created_at_ms - approval_wait.created_at_ms
        )
    elif approval_wait and terminal:
        # Still waiting or ended without resume timestamp
        approval_wait_ms = max(
            0.0, terminal.created_at_ms - approval_wait.created_at_ms
        )

    execution_ms = None
    if running and terminal:
        # Active execution excludes human wait when both markers exist.
        raw = max(0.0, terminal.created_at_ms - running.created_at_ms)
        if approval_wait_ms is not None:
            execution_ms = max(0.0, raw - approval_wait_ms)
        else:
            execution_ms = raw

    total_ms = None
    if submitted and terminal:
        total_ms = max(0.0, terminal.created_at_ms - submitted.created_at_ms)

    notes = [
        "Latency derived from durable event timestamps.",
        "Human approval waiting is separated when markers exist.",
    ]
    if by_type:
        pass
    return LatencyBreakdown(
        queue_wait_ms=queue_wait,
        planning_ms=planning_ms,
        execution_ms=execution_ms,
        approval_wait_ms=approval_wait_ms,
        total_ms=total_ms,
        notes=notes,
    )


def _first(rows: list[JobEvent], event_type: JobEventType) -> JobEvent | None:
    for row in rows:
        if row.event_type is event_type:
            return row
    return None
