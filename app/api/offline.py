"""Offline / demonstration factories — fake LLMs, real persistence & workers.

Labeled explicitly for Docker Compose offline profile. Exercises the real
API → queue → worker → SSE → approval path without paid provider calls.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from app.api.concurrency import CapacityGate
from app.api.config import APISettings, load_api_settings
from app.api.jobs.events import JobEventStore
from app.api.jobs.queue import configure_default_queue
from app.api.jobs.runtime import JobRuntime, set_runtime
from app.api.jobs.store import JobStore
from app.api.main import create_app
from app.api.service import OrchestrationService
from app.approval.service import ApprovalService
from app.approval.store import ApprovalStore
from app.llm.base import LLMClient
from app.llm.demo import DemoLLM
from app.memory.embeddings import HashingEmbedder
from app.memory.service import MemoryService, NullMemoryService
from app.memory.store import MemoryStore

logger = logging.getLogger(__name__)


def _offline_client_scope():
    llm = DemoLLM()

    @contextmanager
    def scope() -> Iterator[LLMClient]:
        yield llm

    return scope


def create_offline_app():
    """Uvicorn factory: ``uvicorn app.api.offline:create_offline_app --factory``."""

    settings = load_api_settings()
    # Offline stack always uses durable enqueue + separate worker.
    settings.jobs_immediate = False
    scope = _offline_client_scope()
    events = JobEventStore(
        settings.events_db_path or settings.jobs_db_path,
        max_events_per_job=settings.events_max_per_job,
    )
    try:
        memory: MemoryService | NullMemoryService = MemoryService(
            MemoryStore(settings.memory_db_path),
            workspace_id=settings.workspace_id,
            retention_days=settings.memory_retention_days,
            embedder=HashingEmbedder(),
        )
    except Exception:
        logger.exception("offline_memory_init_failed")
        memory = NullMemoryService()
    approvals = ApprovalService(
        ApprovalStore(settings.approvals_db_path),
        workspace_id=settings.workspace_id,
        artifact_root=settings.artifact_output_root,
        ttl_ms=settings.approval_ttl_ms,
    )
    service = OrchestrationService(
        client_scope=scope,
        memory=memory,
        memory_top_k=settings.memory_top_k,
        memory_max_context_chars=settings.memory_max_context_chars,
        approval=approvals,
        events=events,
    )
    store = JobStore(settings.jobs_db_path)
    queue = configure_default_queue(settings.queue_db_path, immediate=False)
    gate = CapacityGate(
        max_runs=settings.max_inflight_runs,
        max_tasks=settings.max_inflight_tasks,
    )
    runtime = JobRuntime(
        store=store,
        queue=queue,
        service=service,
        capacity=gate,
        max_run_concurrency=settings.max_run_concurrency,
        max_job_attempts=settings.max_job_attempts,
        lease_ms=settings.job_lease_ms,
        approval=approvals,
        events=events,
        worker_id="offline-api",
    )
    set_runtime(runtime)
    app = create_app(
        api_settings=settings,
        service=service,
        job_runtime=runtime,
        memory_service=memory,
        approval_service=approvals,
        event_store=events,
    )
    app.state.offline_demo = True
    return app


def build_offline_runtime() -> JobRuntime:
    """Worker-side runtime with the same DemoLLM + shared data paths."""

    api = load_api_settings()
    api.jobs_immediate = False
    scope = _offline_client_scope()
    store = JobStore(api.jobs_db_path)
    events = JobEventStore(
        api.events_db_path or api.jobs_db_path,
        max_events_per_job=api.events_max_per_job,
    )
    queue = configure_default_queue(api.queue_db_path, immediate=False)
    capacity = CapacityGate(
        max_runs=api.max_inflight_runs,
        max_tasks=api.max_inflight_tasks,
    )
    try:
        memory: MemoryService | NullMemoryService = MemoryService(
            MemoryStore(api.memory_db_path),
            workspace_id=api.workspace_id,
            retention_days=api.memory_retention_days,
            embedder=HashingEmbedder(),
        )
    except Exception:
        logger.exception("offline_worker_memory_init_failed")
        memory = NullMemoryService()
    approvals = ApprovalService(
        ApprovalStore(api.approvals_db_path),
        workspace_id=api.workspace_id,
        artifact_root=api.artifact_output_root,
        ttl_ms=api.approval_ttl_ms,
    )
    runtime = JobRuntime(
        store=store,
        queue=queue,
        service=OrchestrationService(
            client_scope=scope,
            memory=memory,
            memory_top_k=api.memory_top_k,
            memory_max_context_chars=api.memory_max_context_chars,
            approval=approvals,
            events=events,
        ),
        capacity=capacity,
        max_run_concurrency=api.max_run_concurrency,
        max_job_attempts=api.max_job_attempts,
        lease_ms=api.job_lease_ms,
        approval=approvals,
        events=events,
    )
    set_runtime(runtime)
    return runtime
