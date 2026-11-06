"""FastAPI orchestration service entrypoint.

``POST /v1/runs`` waits until the run finishes (M7, unchanged semantics).
``POST /v1/jobs`` records a durable job and returns HTTP 202 (M8).

Sync engine work for ``/v1/runs`` runs in a worker thread so the event loop
is not blocked. Capacity for that path is reserved until the thread returns.
HTTP disconnect does not cancel that work.

Background jobs hold capacity only inside the worker process while executing;
submission itself does not reserve run capacity, and client disconnect after
202 cannot release worker capacity.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import uuid
from collections.abc import AsyncIterator as TypingAsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.api.concurrency import CapacityExceeded, CapacityGate
from app.api.config import APISettings, load_api_settings
from app.api.readiness import all_ready, check_api_readiness
from app.api.jobs.events import JobEventStore
from app.api.jobs.models import (
    JobListItem,
    JobListResponse,
    JobResultResponse,
    JobStatus,
    JobStatusCounts,
    JobStatusResponse,
    JobSubmitResponse,
    TERMINAL_STATUSES,
)
from app.api.jobs.queue import configure_default_queue
from app.api.jobs.runtime import JobRuntime, set_runtime
from app.api.jobs.store import JobNotFound, JobStore
from app.api.jobs.stream import job_event_sse
from app.api.service import OrchestrationService
from app.api.v1.schemas import (
    ApprovalDecisionRequest,
    ApprovalListResponse,
    ApprovalRejectRequest,
    ApprovalReviseRequest,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    ReadyResponse,
    MemoryDeleteResponse,
    MemoryListResponse,
    MetaResponse,
    RunRequest,
    RunResponse,
)
from app.observability.models import ObservabilitySummary, ReplayTimeline
from app.observability.replay import build_replay_timeline
from app.observability.tracing import configure_tracing
from app.approval.exceptions import (
    ApprovalConflict,
    ApprovalExpired,
    FingerprintMismatch,
)
from app.approval.models import ApprovalRecord
from app.approval.service import ApprovalService
from app.approval.store import ApprovalNotFound, ApprovalStore
from app.memory.service import MemoryService, NullMemoryService
from app.memory.store import MemoryNotFound, MemoryStore, MemoryStoreError

logger = logging.getLogger(__name__)


def create_app(
    *,
    api_settings: APISettings | None = None,
    service: OrchestrationService | None = None,
    job_runtime: JobRuntime | None = None,
    memory_service: MemoryService | NullMemoryService | None = None,
    approval_service: ApprovalService | None = None,
    event_store: JobEventStore | None = None,
) -> FastAPI:
    settings = api_settings or load_api_settings()
    gate = CapacityGate(
        max_runs=settings.max_inflight_runs,
        max_tasks=settings.max_inflight_tasks,
    )

    events_path = settings.events_db_path or settings.jobs_db_path
    events = event_store or JobEventStore(
        events_path,
        max_events_per_job=settings.events_max_per_job,
    )

    if memory_service is not None:
        memory = memory_service
    else:
        try:
            memory = MemoryService(
                MemoryStore(settings.memory_db_path),
                workspace_id=settings.workspace_id,
                retention_days=settings.memory_retention_days,
            )
        except Exception:
            logger.exception("memory_store_init_failed; continuing without memory")
            memory = NullMemoryService()

    if approval_service is not None:
        approvals = approval_service
    else:
        approvals = ApprovalService(
            ApprovalStore(settings.approvals_db_path),
            workspace_id=settings.workspace_id,
            artifact_root=settings.artifact_output_root,
            ttl_ms=settings.approval_ttl_ms,
        )

    if service is not None:
        orchestration = service
    else:
        orchestration = OrchestrationService(
            memory=memory,
            memory_top_k=settings.memory_top_k,
            memory_max_context_chars=settings.memory_max_context_chars,
            approval=approvals,
            events=events,
        )

    if job_runtime is not None:
        runtime = job_runtime
    else:
        store = JobStore(settings.jobs_db_path)
        queue = configure_default_queue(
            settings.queue_db_path,
            immediate=settings.jobs_immediate,
        )
        runtime = JobRuntime(
            store=store,
            queue=queue,
            service=orchestration,
            capacity=gate,
            max_run_concurrency=settings.max_run_concurrency,
            max_job_attempts=settings.max_job_attempts,
            lease_ms=settings.job_lease_ms,
            approval=approvals,
            events=events,
        )
    set_runtime(runtime)
    # Prefer the runtime's event store when a prebuilt runtime is injected.
    if isinstance(getattr(runtime, "events", None), JobEventStore):
        events = runtime.events  # type: ignore[assignment]

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            configure_tracing(
                service_name="agentforge",
                console=settings.otel_console,
                in_memory=True,
            )
        except Exception:
            logger.exception("otel_startup_configure_failed")
        try:
            recovered = runtime.recover()
            if recovered:
                logger.info("startup_recovered_jobs=%s", len(recovered))
        except Exception:
            logger.exception("startup_job_recovery_failed")
        yield

    app = FastAPI(
        title="AgentForge Orchestration Service",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.api_settings = settings
    app.state.capacity_gate = gate
    app.state.orchestration_service = orchestration
    app.state.job_runtime = runtime
    app.state.memory_service = memory
    app.state.approval_service = approvals
    app.state.event_store = events

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="invalid_request",
                    message="The request failed validation.",
                )
            ).model_dump(),
        )

    def require_auth(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        if not settings.auth_required:
            return
        expected = settings.api_token
        assert expected is not None
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="unauthorized",
                        message="Bearer token required.",
                    )
                ).model_dump(),
            )
        provided = authorization.removeprefix("Bearer ").strip()
        if not provided or not secrets.compare_digest(
            provided,
            expected.get_secret_value(),
        ):
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="unauthorized",
                        message="Invalid bearer token.",
                    )
                ).model_dump(),
            )

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Liveness only — process is up. Prefer ``/ready`` for traffic."""

        return HealthResponse()

    @app.get("/ready", response_model=ReadyResponse)
    async def ready() -> JSONResponse:
        """Readiness: SQLite stores and artifact root must be usable."""

        events_path = settings.events_db_path or settings.jobs_db_path
        checks = await asyncio.to_thread(
            check_api_readiness,
            jobs_db_path=settings.jobs_db_path,
            queue_db_path=settings.queue_db_path,
            memory_db_path=settings.memory_db_path,
            approvals_db_path=settings.approvals_db_path,
            events_db_path=str(events_path),
            artifact_root=settings.artifact_output_root,
            job_store=runtime.store,
        )
        offline = bool(getattr(app.state, "offline_demo", False))
        if all_ready(checks):
            return JSONResponse(
                status_code=200,
                content=ReadyResponse(
                    status="ready",
                    checks=checks,
                    offline_demo=offline,
                ).model_dump(),
            )
        return JSONResponse(
            status_code=503,
            content=ReadyResponse(
                status="not_ready",
                checks=checks,
                offline_demo=offline,
                detail="One or more persistence checks failed.",
            ).model_dump(),
        )

    @app.get("/v1/meta", response_model=MetaResponse)
    async def meta(_: None = Depends(require_auth)) -> MetaResponse:
        return MetaResponse(
            auth_required=settings.auth_required,
            workspace_id=settings.workspace_id,
            memory_enabled_default=settings.memory_enabled_default,
            max_inflight_runs=settings.max_inflight_runs,
            max_inflight_tasks=settings.max_inflight_tasks,
            max_run_concurrency=settings.max_run_concurrency,
            notes=[
                "POST /v1/runs waits synchronously until the run finishes.",
                "POST /v1/jobs returns 202 after durable enqueue; poll status/result.",
                "Client disconnect or read timeout on /v1/runs may leave an "
                "uncertain outcome; the service does not cancel worker threads.",
                "HTTP disconnect after job submission cannot release worker "
                "capacity; capacity is held only while a worker executes.",
                "Capacity limits apply to this process only, not across replicas.",
                "A shared bearer token is authentication, not multi-user isolation.",
                "Memory is single-workspace (ORCHESTRATION_WORKSPACE_ID); not "
                "multi-user isolation.",
                "Retrieved memory is untrusted reference data and never "
                "overrides the current task, tools, or approvals.",
                "quality_approved=true means reviewer acceptance; unreviewed "
                "success is not quality-approved.",
                "Tools remain disabled in the HTTP service.",
                "Deletion soft-invalidates rows and clears vectors; running "
                "jobs may still hold a short-term retrieval snapshot taken "
                "at start. Backups are out of band.",
                "Protected actions require POST /v1/jobs + human approval; "
                "M6 quality_approved is not human authorization.",
                "Shared bearer token means shared-operator approval, not "
                "individual attribution.",
                "GET /v1/jobs/{id}/events streams lifecycle SSE (not token "
                "deltas). Use Authorization header; native EventSource cannot "
                "set Bearer — prefer fetch streaming. Shared token is not "
                "multi-user isolation.",
                "GET /v1/jobs lists recent durable jobs with status counts "
                "(dashboard overview; no fabricated metrics).",
                "GET /v1/jobs/{id}/replay is timeline playback from durable "
                "events — not a workflow re-execution; prior approvals are "
                "not reused. Cost figures are estimates from dated fixture "
                "pricing; missing usage is unknown, not zero.",
                "HashingEmbedder memory is lexical/offline — not production "
                "semantic retrieval.",
                f"Telemetry content capture default="
                f"{settings.telemetry_capture_content}; retention policy hint "
                f"{settings.telemetry_retention_days} days "
                "(see app/observability/RETENTION.md).",
            ],
        )

    @app.post("/v1/runs", response_model=RunResponse)
    async def create_run(
        body: RunRequest,
        _: None = Depends(require_auth),
    ) -> RunResponse:
        if body.protected_action is not None:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="approval_requires_jobs",
                        message=(
                            "Protected actions require POST /v1/jobs so the "
                            "workflow can pause for human approval without "
                            "holding an HTTP connection or worker slot."
                        ),
                    ),
                ).model_dump(),
            )
        run_id = str(uuid.uuid4())
        concurrency = min(body.max_concurrency, settings.max_run_concurrency)
        if concurrency < 1:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    run_id=run_id,
                    error=ErrorDetail(
                        code="invalid_request",
                        message="Effective concurrency must be at least 1.",
                    ),
                ).model_dump(),
            )

        try:
            with gate.reserve(concurrency):
                # Sync engine work stays off the event loop. Capacity remains
                # reserved until this thread returns.
                return await asyncio.to_thread(
                    orchestration.run,
                    body,
                    run_id=run_id,
                    concurrency=concurrency,
                )
        except CapacityExceeded:
            raise HTTPException(
                status_code=429,
                detail=ErrorResponse(
                    run_id=run_id,
                    error=ErrorDetail(
                        code="capacity_exceeded",
                        message=(
                            "This process has no remaining run/task capacity. "
                            "Limits apply to this process only."
                        ),
                    ),
                ).model_dump(),
            ) from None
        except HTTPException:
            raise
        except Exception:
            logger.exception("run_id=%s failed", run_id)
            raise HTTPException(
                status_code=500,
                detail=ErrorResponse(
                    run_id=run_id,
                    error=ErrorDetail(
                        code="internal_error",
                        message="The run failed due to an internal error.",
                    ),
                ).model_dump(),
            ) from None

    @app.get("/v1/jobs", response_model=JobListResponse)
    async def list_jobs(
        limit: int = 50,
        offset: int = 0,
        _: None = Depends(require_auth),
    ) -> JobListResponse:
        """Authenticated recent-job list with durable status counts (M14)."""

        if limit < 1 or limit > 200 or offset < 0:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="invalid_request",
                        message="limit must be 1..200 and offset >= 0.",
                    ),
                ).model_dump(),
            )
        try:
            records = await asyncio.to_thread(
                runtime.store.list_recent, limit=limit, offset=offset
            )
            counts_raw = await asyncio.to_thread(runtime.store.status_counts)
        except Exception:
            logger.exception("job_list_failed")
            raise HTTPException(
                status_code=503,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="jobs_unavailable",
                        message="Job store is unavailable.",
                    ),
                ).model_dump(),
            ) from None
        items: list[JobListItem] = []
        for record in records:
            req = record.request if isinstance(record.request, dict) else {}
            goal = req.get("goal")
            if not goal and isinstance(req.get("plan"), dict):
                goal = req["plan"].get("goal")
            preview = None
            if isinstance(goal, str) and goal.strip():
                preview = goal.strip()[:160]
            items.append(
                JobListItem(
                    job_id=record.job_id,
                    status=record.status,
                    attempt=record.attempt,
                    max_attempts=record.max_attempts,
                    created_at_ms=record.created_at_ms,
                    updated_at_ms=record.updated_at_ms,
                    timing=record.timing,
                    error_code=record.error_code,
                    error_message=record.error_message,
                    goal_preview=preview,
                    result_available=record.result is not None
                    or record.status in TERMINAL_STATUSES,
                    review_enabled=req.get("review_enabled")
                    if isinstance(req.get("review_enabled"), bool)
                    else None,
                    memory_enabled=req.get("memory_enabled")
                    if isinstance(req.get("memory_enabled"), bool)
                    else None,
                )
            )
        counts = JobStatusCounts(
            queued=counts_raw.get(JobStatus.QUEUED.value, 0),
            running=counts_raw.get(JobStatus.RUNNING.value, 0),
            succeeded=counts_raw.get(JobStatus.SUCCEEDED.value, 0),
            failed=counts_raw.get(JobStatus.FAILED.value, 0),
            interrupted=counts_raw.get(JobStatus.INTERRUPTED.value, 0),
            awaiting_approval=counts_raw.get(
                JobStatus.AWAITING_APPROVAL.value, 0
            ),
        )
        return JobListResponse(
            items=items, count=len(items), status_counts=counts
        )

    @app.post("/v1/jobs", response_model=JobSubmitResponse, status_code=202)
    async def submit_job(
        body: RunRequest,
        _: None = Depends(require_auth),
    ) -> JobSubmitResponse:
        try:
            record = await asyncio.to_thread(runtime.submit, body)
        except Exception:
            logger.exception("job_submit_failed")
            raise HTTPException(
                status_code=503,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="jobs_unavailable",
                        message=(
                            "Job store or queue infrastructure is unavailable."
                        ),
                    ),
                ).model_dump(),
            ) from None
        return JobSubmitResponse(job_id=record.job_id, status=record.status)

    @app.get("/v1/jobs/{job_id}", response_model=JobStatusResponse)
    async def job_status(
        job_id: str,
        _: None = Depends(require_auth),
    ) -> JobStatusResponse:
        try:
            record = await asyncio.to_thread(runtime.status, job_id)
        except JobNotFound:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="job_not_found",
                        message=f"Unknown job_id '{job_id}'.",
                    ),
                ).model_dump(),
            ) from None
        except Exception:
            logger.exception("job_status_failed job_id=%s", job_id)
            raise HTTPException(
                status_code=503,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="jobs_unavailable",
                        message="Job store is unavailable.",
                    ),
                ).model_dump(),
            ) from None
        return JobStatusResponse(
            job_id=record.job_id,
            status=record.status,
            attempt=record.attempt,
            max_attempts=record.max_attempts,
            timing=record.timing,
            error_code=record.error_code,
            error_message=record.error_message,
            uncertainty=record.uncertainty,
            result_available=record.result is not None
            or record.status in TERMINAL_STATUSES,
        )

    @app.get("/v1/jobs/{job_id}/events", response_class=EventSourceResponse)
    async def job_events(
        job_id: str,
        request: Request,
        _: None = Depends(require_auth),
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> TypingAsyncIterator[ServerSentEvent]:
        """Authenticated SSE of durable lifecycle events for one job.

        Disconnect does not cancel the job or release worker capacity.
        Events are ``stream_kind=lifecycle`` — not fabricated token streams.
        """

        after = 0
        if last_event_id is not None and last_event_id.strip().isdigit():
            after = int(last_event_id.strip())

        async for item in job_event_sse(
            job_id=job_id,
            event_store=events,
            job_store=runtime.store,
            last_event_id=after,
            poll_interval_ms=settings.events_poll_interval_ms,
            heartbeat_seconds=settings.events_heartbeat_seconds,
        ):
            if await request.is_disconnected():
                # Viewer gone — job keeps running.
                break
            yield item

    @app.get("/v1/jobs/{job_id}/replay", response_model=ReplayTimeline)
    async def job_replay(
        job_id: str,
        _: None = Depends(require_auth),
    ) -> ReplayTimeline:
        """Timeline playback from durable events — no models or tools."""

        try:
            await asyncio.to_thread(runtime.status, job_id)
        except JobNotFound:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="job_not_found",
                        message=f"Unknown job_id '{job_id}'.",
                    ),
                ).model_dump(),
            ) from None
        except Exception:
            logger.exception("job_replay_status_failed job_id=%s", job_id)
            raise HTTPException(
                status_code=503,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="jobs_unavailable",
                        message="Job store is unavailable.",
                    ),
                ).model_dump(),
            ) from None
        try:
            return await asyncio.to_thread(
                build_replay_timeline,
                job_id,
                events,
                capture_content=settings.telemetry_capture_content,
            )
        except Exception:
            logger.exception("job_replay_failed job_id=%s", job_id)
            raise HTTPException(
                status_code=503,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="replay_unavailable",
                        message="Event store is unavailable for replay.",
                    ),
                ).model_dump(),
            ) from None

    @app.get(
        "/v1/jobs/{job_id}/observability",
        response_model=ObservabilitySummary,
    )
    async def job_observability(
        job_id: str,
        _: None = Depends(require_auth),
    ) -> ObservabilitySummary:
        """Usage, labeled cost estimate, and latency from a finished result."""

        try:
            record = await asyncio.to_thread(runtime.status, job_id)
        except JobNotFound:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="job_not_found",
                        message=f"Unknown job_id '{job_id}'.",
                    ),
                ).model_dump(),
            ) from None
        except Exception:
            logger.exception("job_observability_failed job_id=%s", job_id)
            raise HTTPException(
                status_code=503,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="jobs_unavailable",
                        message="Job store is unavailable.",
                    ),
                ).model_dump(),
            ) from None
        if record.result is None:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="observability_not_ready",
                        message=(
                            "Observability summary is available after the job "
                            "has a stored result (succeeded, failed, or "
                            "awaiting_approval with a partial result)."
                        ),
                    ),
                ).model_dump(),
            )
        raw = record.result.get("observability")
        if raw is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="observability_missing",
                        message=(
                            "Stored result has no observability summary "
                            "(pre-M12 job or incomplete capture)."
                        ),
                    ),
                ).model_dump(),
            )
        return ObservabilitySummary.model_validate(raw)

    @app.get("/v1/jobs/{job_id}/result", response_model=JobResultResponse)
    async def job_result(
        job_id: str,
        _: None = Depends(require_auth),
    ) -> JobResultResponse:
        try:
            record = await asyncio.to_thread(runtime.status, job_id)
        except JobNotFound:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="job_not_found",
                        message=f"Unknown job_id '{job_id}'.",
                    ),
                ).model_dump(),
            ) from None
        except Exception:
            logger.exception("job_result_failed job_id=%s", job_id)
            raise HTTPException(
                status_code=503,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="jobs_unavailable",
                        message="Job store is unavailable.",
                    ),
                ).model_dump(),
            ) from None
        # Terminal jobs always expose results. Awaiting-approval may expose a
        # partial checkpoint result so the dashboard can show plan/tasks while
        # human authorization is pending (M14).
        if record.status not in TERMINAL_STATUSES:
            if not (
                record.status is JobStatus.AWAITING_APPROVAL
                and record.result is not None
            ):
                raise HTTPException(
                    status_code=409,
                    detail=ErrorResponse(
                        error=ErrorDetail(
                            code="job_not_ready",
                            message=(
                                f"Job is {record.status.value}; result is available "
                                "after succeeded/failed, or as a partial checkpoint "
                                "while awaiting_approval."
                            ),
                        ),
                    ).model_dump(),
                )
        return JobResultResponse(
            job_id=record.job_id,
            status=record.status,
            result=record.result,
            error_code=record.error_code,
            error_message=record.error_message,
            uncertainty=record.uncertainty,
            timing=record.timing,
        )

    @app.get("/v1/memory", response_model=MemoryListResponse)
    async def list_memory(
        limit: int = 50,
        offset: int = 0,
        _: None = Depends(require_auth),
    ) -> MemoryListResponse:
        if limit < 1 or limit > 200 or offset < 0:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="invalid_request",
                        message="limit must be 1..200 and offset >= 0.",
                    ),
                ).model_dump(),
            )
        try:
            items = await asyncio.to_thread(
                memory.list_memories,
                limit=limit,
                offset=offset,
            )
        except Exception:
            logger.exception("memory_list_failed")
            raise HTTPException(
                status_code=503,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="memory_unavailable",
                        message="Memory storage is unavailable.",
                    ),
                ).model_dump(),
            ) from None
        return MemoryListResponse(
            workspace_id=settings.workspace_id,
            items=items,
            count=len(items),
        )

    @app.delete("/v1/memory/{memory_id}", response_model=MemoryDeleteResponse)
    async def delete_memory(
        memory_id: str,
        _: None = Depends(require_auth),
    ) -> MemoryDeleteResponse:
        try:
            await asyncio.to_thread(memory.delete, memory_id)
        except MemoryNotFound:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="memory_not_found",
                        message=f"Unknown memory_id '{memory_id}'.",
                    ),
                ).model_dump(),
            ) from None
        except MemoryStoreError:
            raise HTTPException(
                status_code=503,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="memory_unavailable",
                        message="Memory storage is unavailable.",
                    ),
                ).model_dump(),
            ) from None
        except Exception:
            logger.exception("memory_delete_failed id=%s", memory_id)
            raise HTTPException(
                status_code=503,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="memory_unavailable",
                        message="Memory storage is unavailable.",
                    ),
                ).model_dump(),
            ) from None
        return MemoryDeleteResponse(
            workspace_id=settings.workspace_id,
            deleted=1,
            memory_id=memory_id,
        )

    @app.delete("/v1/memory", response_model=MemoryDeleteResponse)
    async def delete_all_memory(
        _: None = Depends(require_auth),
    ) -> MemoryDeleteResponse:
        try:
            deleted = await asyncio.to_thread(memory.delete_all)
        except MemoryStoreError:
            raise HTTPException(
                status_code=503,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="memory_unavailable",
                        message="Memory storage is unavailable.",
                    ),
                ).model_dump(),
            ) from None
        except Exception:
            logger.exception("memory_delete_all_failed")
            raise HTTPException(
                status_code=503,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="memory_unavailable",
                        message="Memory storage is unavailable.",
                    ),
                ).model_dump(),
            ) from None
        return MemoryDeleteResponse(
            workspace_id=settings.workspace_id,
            deleted=deleted,
        )

    @app.get("/v1/approvals", response_model=ApprovalListResponse)
    async def list_approvals(
        limit: int = 50,
        _: None = Depends(require_auth),
    ) -> ApprovalListResponse:
        if limit < 1 or limit > 200:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="invalid_request",
                        message="limit must be 1..200.",
                    ),
                ).model_dump(),
            )
        items = await asyncio.to_thread(approvals.list_pending, limit=limit)
        return ApprovalListResponse(
            workspace_id=settings.workspace_id,
            items=items,
            count=len(items),
        )

    @app.get("/v1/approvals/{approval_id}", response_model=ApprovalRecord)
    async def get_approval(
        approval_id: str,
        _: None = Depends(require_auth),
    ) -> ApprovalRecord:
        try:
            return await asyncio.to_thread(approvals.get, approval_id)
        except ApprovalNotFound:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="approval_not_found",
                        message=f"Unknown approval_id '{approval_id}'.",
                    ),
                ).model_dump(),
            ) from None
        except ApprovalConflict as exc:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="approval_not_found",
                        message=str(exc),
                    ),
                ).model_dump(),
            ) from None

    @app.post("/v1/approvals/{approval_id}/approve", response_model=ApprovalRecord)
    async def approve_request(
        approval_id: str,
        body: ApprovalDecisionRequest,
        _: None = Depends(require_auth),
    ) -> ApprovalRecord:
        try:
            record = await asyncio.to_thread(
                approvals.approve,
                approval_id,
                fingerprint=body.fingerprint,
                note=body.note,
            )
        except ApprovalNotFound:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="approval_not_found",
                        message=f"Unknown approval_id '{approval_id}'.",
                    ),
                ).model_dump(),
            ) from None
        except FingerprintMismatch as exc:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="fingerprint_mismatch",
                        message=str(exc),
                    ),
                ).model_dump(),
            ) from None
        except ApprovalExpired as exc:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="approval_expired",
                        message=str(exc),
                    ),
                ).model_dump(),
            ) from None
        except ApprovalConflict as exc:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="approval_conflict",
                        message=str(exc),
                    ),
                ).model_dump(),
            ) from None
        await asyncio.to_thread(
            runtime.resume_after_approval,
            record.job_id,
            fingerprint=record.fingerprint,
        )
        return record

    @app.post("/v1/approvals/{approval_id}/reject", response_model=ApprovalRecord)
    async def reject_request(
        approval_id: str,
        body: ApprovalRejectRequest,
        _: None = Depends(require_auth),
    ) -> ApprovalRecord:
        try:
            record = await asyncio.to_thread(
                approvals.reject,
                approval_id,
                note=body.note,
            )
        except ApprovalNotFound:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="approval_not_found",
                        message=f"Unknown approval_id '{approval_id}'.",
                    ),
                ).model_dump(),
            ) from None
        except ApprovalConflict as exc:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="approval_conflict",
                        message=str(exc),
                    ),
                ).model_dump(),
            ) from None
        try:
            await asyncio.to_thread(
                runtime.store.mark_failed_from_any,
                record.job_id,
                error_code="approval_rejected",
                error_message=body.note or "Human operator rejected the proposal.",
            )
        except Exception:
            logger.exception(
                "approval_reject_job_fail job_id=%s", record.job_id
            )
        return record

    @app.post("/v1/approvals/{approval_id}/revise", response_model=ApprovalRecord)
    async def revise_request(
        approval_id: str,
        body: ApprovalReviseRequest,
        _: None = Depends(require_auth),
    ) -> ApprovalRecord:
        try:
            record = await asyncio.to_thread(
                approvals.revise,
                approval_id,
                action=body.action,
            )
        except ApprovalNotFound:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="approval_not_found",
                        message=f"Unknown approval_id '{approval_id}'.",
                    ),
                ).model_dump(),
            ) from None
        except ApprovalConflict as exc:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code="approval_conflict",
                        message=str(exc),
                    ),
                ).model_dump(),
            ) from None

        def _sync_checkpoint() -> None:
            job = runtime.store.get(record.job_id)
            if job.status.value != "awaiting_approval":
                return
            checkpoint = dict(job.checkpoint or {})
            checkpoint["action"] = body.action.model_dump(mode="json")
            checkpoint["approval_id"] = record.approval_id
            checkpoint["fingerprint"] = record.fingerprint
            # Clear any stale approved fingerprint — revision needs fresh approve.
            checkpoint.pop("approved_fingerprint", None)
            runtime.store.update_checkpoint(
                record.job_id,
                checkpoint=checkpoint,
                expected_status=job.status,
            )

        try:
            await asyncio.to_thread(_sync_checkpoint)
        except Exception:
            logger.exception("revise_checkpoint_sync_failed job_id=%s", record.job_id)
        return record

    return app


# Prefer: uvicorn app.api.main:create_app --factory
# Avoid creating the app at import time so tests can supply settings.
