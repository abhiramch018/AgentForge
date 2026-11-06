"""Job submission, delivery handling, lease recovery, and HITL resume."""

from __future__ import annotations

import logging
import socket
import threading
import uuid
from typing import Any

from pydantic import ValidationError

from app.api.concurrency import CapacityExceeded, CapacityGate
from app.api.jobs.events import JobEventStore, JobEventType, NullEventStore
from app.api.jobs.models import JobRecord, JobStatus, PAUSED_STATUSES, TERMINAL_STATUSES
from app.api.jobs.queue import JobQueue
from app.api.jobs.store import JobStore
from app.api.service import OrchestrationService
from app.api.v1.schemas import RunRequest
from app.approval.exceptions import ApprovalRequired
from app.approval.service import ApprovalService
from app.observability import metrics as metrics_mod
from app.observability.context import bind_approval_waiting, bind_run
from app.observability.logging import ops_log
from app.observability.tracing import (
    attach_trace_context,
    detach_trace_context,
    inject_trace_context,
    start_span,
)

logger = logging.getLogger(__name__)

EventSink = JobEventStore | NullEventStore

_runtime_lock = threading.Lock()
_runtime: "JobRuntime | None" = None


def get_runtime() -> "JobRuntime":
    if _runtime is None:
        raise RuntimeError("Job runtime is not configured.")
    return _runtime


def set_runtime(runtime: "JobRuntime | None") -> None:
    global _runtime
    with _runtime_lock:
        _runtime = runtime


class JobRuntime:
    """Coordinates durable store, queue delivery, and engine execution."""

    def __init__(
        self,
        *,
        store: JobStore,
        queue: JobQueue,
        service: OrchestrationService,
        capacity: CapacityGate,
        max_run_concurrency: int,
        max_job_attempts: int = 2,
        lease_ms: float = 120_000,
        worker_id: str | None = None,
        approval: ApprovalService | None = None,
        events: EventSink | None = None,
    ) -> None:
        self.store = store
        self.queue = queue
        self.service = service
        self.capacity = capacity
        self.max_run_concurrency = max_run_concurrency
        self.max_job_attempts = max_job_attempts
        self.lease_ms = lease_ms
        self.worker_id = worker_id or f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
        self.approval = approval
        self.events: EventSink = events or NullEventStore()

    def _emit(
        self,
        job_id: str,
        event_type: JobEventType,
        payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.events.append(job_id, event_type, payload)
        except Exception:
            logger.exception(
                "job_event_emit_failed job_id=%s type=%s", job_id, event_type
            )

    def submit(self, request: RunRequest) -> JobRecord:
        """Persist the job, then enqueue delivery.

        Order: durable INSERT first, then queue publish. If publish fails, the
        job remains ``queued`` and recovery will re-enqueue it.
        """

        with start_span("job.submit"):
            carrier = inject_trace_context()
            record = self.store.create_queued_job(
                request.model_dump(mode="json"),
                max_attempts=self.max_job_attempts,
                trace_context=carrier or None,
            )
            # Event after durable create — reflects committed queued state.
            self._emit(
                record.job_id,
                JobEventType.JOB_SUBMITTED,
                {"status": "queued"},
            )
            metrics_mod.incr("jobs_submitted", labels={"status": "queued"})
            ops_log("job_submitted", job_id=record.job_id)
            try:
                self.queue.enqueue(record.job_id)
            except Exception:
                logger.exception(
                    "job_id=%s durable_create_ok enqueue_failed; recovery will retry",
                    record.job_id,
                )
            return self.store.get(record.job_id)

    def recover(self) -> list[str]:
        """Re-enqueue interrupted/queued jobs and expired leases.

        Jobs in ``awaiting_approval`` are intentionally not re-enqueued —
        they hold no worker lease and wait for a human decision.
        """

        requeued = self.store.recover_expired_leases()
        pending = self.store.list_needs_delivery()
        seen: set[str] = set()
        ordered: list[str] = []
        for job_id in [*requeued, *pending]:
            if job_id not in seen:
                seen.add(job_id)
                ordered.append(job_id)
        for job_id in ordered:
            try:
                self.queue.enqueue(job_id)
            except Exception:
                logger.exception("job_id=%s recovery_enqueue_failed", job_id)
        return ordered

    def resume_after_approval(self, job_id: str, *, fingerprint: str) -> JobRecord:
        """Queue a paused job after a matching approval decision."""

        record = self.store.get(job_id)
        if record.status is JobStatus.AWAITING_APPROVAL:
            checkpoint = dict(record.checkpoint or {})
            checkpoint["approved_fingerprint"] = fingerprint
            checkpoint["phase"] = "awaiting_protected_action"
            self.store.update_checkpoint(
                job_id,
                checkpoint=checkpoint,
                expected_status=JobStatus.AWAITING_APPROVAL,
            )
            record = self.store.queue_for_resume(job_id)
            self._emit(
                job_id,
                JobEventType.APPROVAL_RESUMED,
                {"fingerprint": fingerprint, "status": "queued"},
            )
            ops_log("approval_resumed", job_id=job_id)
        try:
            self.queue.enqueue(job_id)
        except Exception:
            logger.exception("job_id=%s resume_enqueue_failed", job_id)
        return self.store.get(job_id)

    def handle_delivery(self, job_id: str) -> None:
        """Handle one queue message. Safe under at-least-once delivery."""

        try:
            record = self.store.get(job_id)
        except Exception:
            logger.exception("job_id=%s delivery_lookup_failed", job_id)
            return

        if record.status in TERMINAL_STATUSES:
            logger.info("job_id=%s delivery_ignored (terminal)", job_id)
            return
        if record.status in PAUSED_STATUSES:
            logger.info("job_id=%s delivery_ignored (awaiting_approval)", job_id)
            return
        if (
            record.status is JobStatus.RUNNING
            and record.lease_expires_at_ms is not None
            and record.lease_expires_at_ms > _now_ms()
        ):
            logger.info("job_id=%s delivery_ignored (active lease)", job_id)
            return

        try:
            request = RunRequest.model_validate(record.request)
        except ValidationError:
            claimed = self.store.try_claim(
                job_id,
                owner_id=self.worker_id,
                lease_ms=self.lease_ms,
            )
            if claimed is None:
                return
            self.store.mark_failed(
                job_id,
                owner_id=self.worker_id,
                error_code="invalid_request",
                error_message="Stored job request failed validation.",
            )
            return

        concurrency = min(request.max_concurrency, self.max_run_concurrency)
        if concurrency < 1:
            claimed = self.store.try_claim(
                job_id,
                owner_id=self.worker_id,
                lease_ms=self.lease_ms,
            )
            if claimed is None:
                return
            self.store.mark_failed(
                job_id,
                owner_id=self.worker_id,
                error_code="invalid_request",
                error_message="Effective concurrency must be at least 1.",
            )
            return

        # Post-approval resume must not burn infrastructure retry budget.
        is_resume = bool(
            record.checkpoint
            and record.checkpoint.get("approved_fingerprint")
            and record.checkpoint.get("phase") == "awaiting_protected_action"
        )

        token = attach_trace_context(record.trace_context)
        try:
            with bind_run(job_id), start_span(
                "job.handle_delivery",
                attributes={"resume": is_resume},
            ):
                try:
                    with self.capacity.reserve(concurrency):
                        claimed = self.store.try_claim(
                            job_id,
                            owner_id=self.worker_id,
                            lease_ms=self.lease_ms,
                            count_attempt=not is_resume,
                        )
                        if claimed is None:
                            logger.info(
                                "job_id=%s delivery_ignored (not claimable)", job_id
                            )
                            return
                        if claimed.timing.queue_wait_ms is not None:
                            metrics_mod.observe_ms(
                                "queue_wait_ms",
                                claimed.timing.queue_wait_ms,
                            )
                        if not is_resume and claimed.attempt > 1:
                            metrics_mod.incr(
                                "retries", labels={"phase": "worker"}
                            )
                        self._emit(
                            job_id,
                            JobEventType.JOB_RUNNING,
                            {"attempt": claimed.attempt, "resume": is_resume},
                        )
                        ops_log(
                            "job_running",
                            job_id=job_id,
                            attempt=claimed.attempt,
                            resume=is_resume,
                        )
                        self._execute_claimed(
                            job_id,
                            request,
                            concurrency,
                            checkpoint=claimed.checkpoint,
                        )
                except CapacityExceeded:
                    metrics_mod.incr(
                        "capacity_deferred", labels={"phase": "worker"}
                    )
                    logger.info(
                        "job_id=%s capacity_deferred scope=worker_process",
                        job_id,
                    )
                    try:
                        self.queue.enqueue(job_id)
                    except Exception:
                        logger.exception(
                            "job_id=%s capacity_reenqueues_failed", job_id
                        )
        finally:
            detach_trace_context(token)

    def _execute_claimed(
        self,
        job_id: str,
        request: RunRequest,
        concurrency: int,
        *,
        checkpoint: dict[str, Any] | None,
    ) -> None:
        stop = threading.Event()

        def heartbeat_loop() -> None:
            interval = max(1.0, self.lease_ms / 3000.0)
            while not stop.wait(interval):
                if not self.store.heartbeat(
                    job_id,
                    owner_id=self.worker_id,
                    lease_ms=self.lease_ms,
                ):
                    return

        thread = threading.Thread(target=heartbeat_loop, daemon=True)
        thread.start()
        try:
            with start_span("job.execute"):
                response = self.service.run(
                    request,
                    run_id=job_id,
                    concurrency=concurrency,
                    checkpoint=checkpoint,
                )
            self.store.mark_succeeded(
                job_id,
                owner_id=self.worker_id,
                result=response.model_dump(mode="json"),
            )
            self._emit(
                job_id,
                JobEventType.JOB_SUCCEEDED,
                {"status": response.status.value},
            )
            metrics_mod.incr(
                "jobs_completed", labels={"status": "succeeded"}
            )
            if response.timing.execute_ms is not None:
                metrics_mod.observe_ms("execution_ms", response.timing.execute_ms)
            ops_log("job_succeeded", job_id=job_id, status=response.status.value)
        except ApprovalRequired as pause:
            with bind_approval_waiting(True):
                # Persist proposal checkpoint and release the worker slot.
                self.store.mark_awaiting_approval(
                    job_id,
                    owner_id=self.worker_id,
                    checkpoint=pause.checkpoint,
                    result=pause.checkpoint.get("run_response"),
                )
                self._emit(
                    job_id,
                    JobEventType.APPROVAL_WAITING,
                    {
                        "approval_id": pause.approval_id,
                        "fingerprint": pause.fingerprint,
                    },
                )
                ops_log(
                    "approval_waiting",
                    job_id=job_id,
                    approval_id=pause.approval_id,
                )
            logger.info(
                "job_id=%s awaiting_approval approval_id=%s (capacity released)",
                job_id,
                pause.approval_id,
            )
        except Exception as exc:
            logger.exception("job_id=%s execution_failed", job_id)
            self.store.mark_failed(
                job_id,
                owner_id=self.worker_id,
                error_code="execution_failed",
                error_message="The job failed during execution.",
                uncertainty=(
                    "The worker raised during execution; an LLM or tool call "
                    "may have completed with uncertain effect. "
                    f"detail_type={type(exc).__name__}"
                ),
            )
            self._emit(
                job_id,
                JobEventType.JOB_FAILED,
                {
                    "error_code": "execution_failed",
                    "detail_type": type(exc).__name__,
                },
            )
            metrics_mod.incr("jobs_failed", labels={"status": "failed"})
            ops_log(
                "job_failed",
                job_id=job_id,
                detail_type=type(exc).__name__,
            )
        finally:
            stop.set()

    def status(self, job_id: str) -> JobRecord:
        return self.store.get(job_id)

    def result_payload(self, job_id: str) -> dict[str, Any] | None:
        record = self.store.get(job_id)
        return record.result


def _now_ms() -> float:
    import time

    return time.time() * 1000
