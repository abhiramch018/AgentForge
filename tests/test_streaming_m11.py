"""M11 durable job event streaming (fake LLMs, local HTTP)."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.api.concurrency import CapacityGate
from app.api.config import APISettings
from app.api.jobs.events import JobEventStore, JobEventType
from app.api.jobs.queue import JobQueue
from app.api.jobs.runtime import JobRuntime, set_runtime
from app.api.jobs.store import JobStore
from app.api.main import create_app
from app.api.service import OrchestrationService
from app.api.v1.schemas import PlanInput, RunRequest
from app.core.messages import ChatMessage
from app.llm.base import LLMClient, LLMResponse
from app.memory.service import NullMemoryService


class ScriptedLLM(LLMClient):
    def __init__(self, answers: Sequence[str] | None = None) -> None:
        self._answers = list(answers or [])

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        content = self._answers.pop(0) if self._answers else "ok"
        return LLMResponse(content=content, model="fake-model")


def _settings(**kwargs: object) -> APISettings:
    base = {
        "api_token": "test-token",
        "allow_unauthenticated_local": False,
        "max_inflight_runs": 2,
        "max_inflight_tasks": 8,
        "max_run_concurrency": 4,
        "jobs_immediate": True,
        "events_poll_interval_ms": 50.0,
        "events_heartbeat_seconds": 1.0,
        "events_max_per_job": 50,
    }
    base.update(kwargs)
    return APISettings(_env_file=None, **base)  # type: ignore[arg-type]


def _plan() -> PlanInput:
    return PlanInput(
        goal="Stream demo",
        tasks=[
            {
                "id": "t1",
                "description": "Do work",
                "role": "general",
                "depends_on": [],
                "expected_output": "Done",
            }
        ],
    )


def _runtime(tmp_path: Path, answers: Sequence[str] | None = None) -> JobRuntime:
    events = JobEventStore(tmp_path / "jobs.db", max_events_per_job=50)
    llm = ScriptedLLM(answers)

    @contextmanager
    def scope() -> Iterator[LLMClient]:
        yield llm

    service = OrchestrationService(
        client_scope=scope,
        memory=NullMemoryService(),
        events=events,
    )
    runtime = JobRuntime(
        store=JobStore(tmp_path / "jobs.db"),
        queue=JobQueue(tmp_path / "huey.db", immediate=True),
        service=service,
        capacity=CapacityGate(max_runs=2, max_tasks=8),
        max_run_concurrency=4,
        events=events,
        worker_id="stream-worker",
    )
    set_runtime(runtime)
    return runtime


AUTH = {"Authorization": "Bearer test-token"}


def test_event_order_for_successful_job(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, ["hello"])
    job = runtime.submit(
        RunRequest(api_version="v1", plan=_plan(), memory_enabled=False)
    )
    assert job.status.value == "succeeded"
    events = runtime.events.list_since(job.job_id, after_event_id=0, limit=100)
    types = [e.event_type for e in events]
    assert JobEventType.JOB_SUBMITTED in types
    assert JobEventType.JOB_RUNNING in types
    assert JobEventType.TASK_STARTED in types
    assert JobEventType.TASK_COMPLETED in types
    assert JobEventType.JOB_SUCCEEDED in types
    assert types.index(JobEventType.JOB_SUBMITTED) < types.index(
        JobEventType.JOB_RUNNING
    )
    assert types.index(JobEventType.TASK_STARTED) < types.index(
        JobEventType.TASK_COMPLETED
    )
    for e in events:
        assert e.stream_kind == "lifecycle"


def test_replay_from_last_event_id(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, ["a"])
    job = runtime.submit(
        RunRequest(api_version="v1", plan=_plan(), memory_enabled=False)
    )
    all_events = runtime.events.list_since(job.job_id, after_event_id=0)
    mid = all_events[1].event_id
    replay = runtime.events.list_since(job.job_id, after_event_id=mid)
    assert all(e.event_id > mid for e in replay)
    assert replay[0].event_id == all_events[2].event_id


def test_replay_gap_when_history_expired(tmp_path: Path) -> None:
    events = JobEventStore(tmp_path / "e.db", max_events_per_job=3)
    for i in range(6):
        events.append("job-x", JobEventType.TASK_STARTED, {"i": i})
    earliest = events.earliest_id("job-x")
    assert earliest is not None
    # Client asks for id older than retention.
    remaining = events.list_since("job-x", after_event_id=0)
    assert len(remaining) == 3
    assert remaining[0].event_id == earliest


def test_stream_requires_auth(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, ["x"])
    app = create_app(
        api_settings=_settings(
            jobs_db_path=str(tmp_path / "jobs.db"),
            queue_db_path=str(tmp_path / "huey.db"),
        ),
        service=runtime.service,
        job_runtime=runtime,
        event_store=runtime.events,  # type: ignore[arg-type]
        memory_service=NullMemoryService(),
    )
    with TestClient(app) as client:
        assert client.get("/v1/jobs/nope/events").status_code == 401


def test_sse_http_stream_fake_llm(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, ["stream-ok"])
    app = create_app(
        api_settings=_settings(
            jobs_db_path=str(tmp_path / "jobs.db"),
            queue_db_path=str(tmp_path / "huey.db"),
            events_poll_interval_ms=40.0,
            events_heartbeat_seconds=30.0,
        ),
        service=runtime.service,
        job_runtime=runtime,
        event_store=runtime.events,  # type: ignore[arg-type]
        memory_service=NullMemoryService(),
    )
    sock_port = {"p": 0}

    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    sock_port["p"] = s.getsockname()[1]
    s.close()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=sock_port["p"], log_level="error"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{sock_port['p']}"
    for _ in range(50):
        try:
            if httpx.get(f"{base}/health", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        raise AssertionError("server failed to start")

    try:
        job = runtime.submit(
            RunRequest(api_version="v1", plan=_plan(), memory_enabled=False)
        )
        seen: list[str] = []
        with httpx.Client(timeout=10.0) as client:
            with client.stream(
                "GET",
                f"{base}/v1/jobs/{job.job_id}/events",
                headers=AUTH,
            ) as response:
                assert response.status_code == 200
                for line in response.iter_lines():
                    if line.startswith("event:"):
                        seen.append(line.split(":", 1)[1].strip())
                    if "job.succeeded" in seen:
                        break
        assert "job.submitted" in seen or "job.succeeded" in seen
        assert "job.succeeded" in seen
        # Disconnect viewer — job already terminal; capacity free.
        assert runtime.capacity.snapshot()["active_runs"] == 0
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_disconnected_viewer_does_not_hold_capacity(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, ["done"])
    before = runtime.capacity.snapshot()["active_runs"]
    runtime.submit(
        RunRequest(api_version="v1", plan=_plan(), memory_enabled=False)
    )
    assert runtime.capacity.snapshot()["active_runs"] == before


def test_approval_waiting_event(tmp_path: Path) -> None:
    from app.approval.models import ProtectedAction
    from app.approval.service import ApprovalService
    from app.approval.store import ApprovalStore

    events = JobEventStore(tmp_path / "jobs.db")
    llm = ScriptedLLM(["draft"])

    @contextmanager
    def scope() -> Iterator[LLMClient]:
        yield llm

    approvals = ApprovalService(
        ApprovalStore(tmp_path / "appr.db"),
        workspace_id="ws",
        artifact_root=str(tmp_path / "art"),
    )
    service = OrchestrationService(
        client_scope=scope,
        memory=NullMemoryService(),
        approval=approvals,
        events=events,
    )
    runtime = JobRuntime(
        store=JobStore(tmp_path / "jobs.db"),
        queue=JobQueue(tmp_path / "huey.db", immediate=True),
        service=service,
        capacity=CapacityGate(max_runs=2, max_tasks=8),
        max_run_concurrency=4,
        events=events,
        approval=approvals,
    )
    set_runtime(runtime)
    job = runtime.submit(
        RunRequest(
            api_version="v1",
            plan=_plan(),
            memory_enabled=False,
            protected_action=ProtectedAction(
                relative_path="a.txt",
                content="x",
                reason="demo",
            ),
        )
    )
    assert job.status.value == "awaiting_approval"
    types = [e.event_type for e in events.list_since(job.job_id, after_event_id=0)]
    assert JobEventType.APPROVAL_WAITING in types
    assert runtime.capacity.snapshot()["active_runs"] == 0
