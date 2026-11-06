"""M8 background jobs: store, queue delivery, API, recovery (fake LLMs)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.client import OrchestrationClient
from app.api.concurrency import CapacityGate
from app.api.config import APISettings
from app.api.jobs.models import JobStatus
from app.api.jobs.queue import JobQueue
from app.api.jobs.runtime import JobRuntime, set_runtime
from app.api.jobs.store import JobStore
from app.api.main import create_app
from app.api.service import OrchestrationService
from app.api.v1.schemas import PlanInput, RunRequest
from app.core.messages import ChatMessage
from app.llm.base import LLMClient, LLMResponse
from app.orchestration import TaskStatus


class ScriptedLLM(LLMClient):
    def __init__(self, answers: Sequence[str] | None = None) -> None:
        self._answers = list(answers or [])
        self.calls = 0

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        self.calls += 1
        if self._answers:
            content = self._answers.pop(0)
        else:
            content = "specialist output"
        return LLMResponse(content=content, model="fake-model")


class BlockingLLM(LLMClient):
    def __init__(self, entered: threading.Event, release: threading.Event) -> None:
        self.entered = entered
        self.release = release
        self.calls = 0

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        self.calls += 1
        self.entered.set()
        self.release.wait(timeout=5)
        return LLMResponse(content="done", model="fake-model")


def _review_json(decision: str) -> str:
    return json.dumps(
        {
            "decision": decision,
            "feedback": "ok" if decision == "accepted" else "needs work",
            "issues": [] if decision == "accepted" else ["gap"],
            "task_coverage": "ok",
            "expected_output_fit": "ok",
            "internal_consistency": "ok",
            "evidence_support": "ok",
            "limitations": "no independent fact check",
            "checks_supplied_evidence_only": True,
            "independently_verified_facts": False,
        }
    )


def _settings(**kwargs: object) -> APISettings:
    base = {
        "api_token": "test-token",
        "allow_unauthenticated_local": False,
        "max_inflight_runs": 2,
        "max_inflight_tasks": 8,
        "max_run_concurrency": 4,
        "jobs_immediate": True,
        "max_job_attempts": 2,
        "job_lease_ms": 5_000.0,
    }
    base.update(kwargs)
    return APISettings(_env_file=None, **base)  # type: ignore[arg-type]


def _service(answers: Sequence[str] | None = None) -> OrchestrationService:
    llm = ScriptedLLM(answers)

    @contextmanager
    def scope() -> Iterator[LLMClient]:
        yield llm

    return OrchestrationService(client_scope=scope)


def _simple_plan() -> PlanInput:
    return PlanInput(
        goal="Demo goal",
        tasks=[
            {
                "id": "only",
                "description": "Do the task",
                "role": "general",
                "depends_on": [],
                "expected_output": "A short answer",
            }
        ],
    )


def _runtime(
    tmp_path: Path,
    service: OrchestrationService,
    *,
    immediate: bool = True,
    max_runs: int = 2,
    max_tasks: int = 8,
    max_attempts: int = 2,
    lease_ms: float = 5_000.0,
) -> JobRuntime:
    store = JobStore(tmp_path / "jobs.db")
    queue = JobQueue(tmp_path / "huey.db", immediate=immediate)
    capacity = CapacityGate(max_runs=max_runs, max_tasks=max_tasks)
    runtime = JobRuntime(
        store=store,
        queue=queue,
        service=service,
        capacity=capacity,
        max_run_concurrency=4,
        max_job_attempts=max_attempts,
        lease_ms=lease_ms,
        worker_id="test-worker",
    )
    set_runtime(runtime)
    return runtime


AUTH = {"Authorization": "Bearer test-token"}


def test_job_success_submit_poll_result(tmp_path: Path) -> None:
    service = _service(["ok"])
    runtime = _runtime(tmp_path, service)
    app = create_app(
        api_settings=_settings(jobs_db_path=str(tmp_path / "jobs.db")),
        service=service,
        job_runtime=runtime,
    )
    with TestClient(app) as client:
        submit = client.post(
            "/v1/jobs",
            headers=AUTH,
            json={
                "api_version": "v1",
                "plan": _simple_plan().model_dump(mode="json"),
            },
        )
        assert submit.status_code == 202
        job_id = submit.json()["job_id"]
        assert submit.json()["status"] == "queued" or submit.json()["status"] in {
            "queued",
            "succeeded",
            "running",
        }

        # Immediate Huey runs inline on enqueue; status should be terminal soon.
        status = client.get(f"/v1/jobs/{job_id}", headers=AUTH)
        assert status.status_code == 200
        body = status.json()
        assert body["status"] == "succeeded"
        assert body["timing"]["submitted_at_ms"] is not None
        assert body["timing"]["queue_wait_ms"] is not None
        assert body["timing"]["execute_ms"] is not None
        assert body["timing"]["total_ms"] is not None

        result = client.get(f"/v1/jobs/{job_id}/result", headers=AUTH)
        assert result.status_code == 200
        payload = result.json()
        assert payload["status"] == "succeeded"
        assert payload["result"]["execution"]["task_results"][0]["status"] == "succeeded"
        assert (
            payload["result"]["execution"]["task_results"][0]["quality_approved"]
            is False
        )


def test_list_jobs_returns_recent_and_status_counts(tmp_path: Path) -> None:
    service = _service(["first", "second"])
    runtime = _runtime(tmp_path, service)
    app = create_app(
        api_settings=_settings(jobs_db_path=str(tmp_path / "jobs.db")),
        service=service,
        job_runtime=runtime,
    )
    with TestClient(app) as client:
        assert client.get("/v1/jobs", headers=AUTH).status_code == 200
        empty = client.get("/v1/jobs", headers=AUTH).json()
        assert empty["count"] == 0
        assert empty["status_counts"]["succeeded"] == 0

        for _ in range(2):
            submit = client.post(
                "/v1/jobs",
                headers=AUTH,
                json={
                    "api_version": "v1",
                    "plan": _simple_plan().model_dump(mode="json"),
                    "memory_enabled": False,
                },
            )
            assert submit.status_code == 202

        listed = client.get("/v1/jobs?limit=10", headers=AUTH)
        assert listed.status_code == 200
        body = listed.json()
        assert body["count"] == 2
        assert body["status_counts"]["succeeded"] == 2
        assert all(item["goal_preview"] for item in body["items"])
        assert client.get("/v1/jobs", headers={}).status_code == 401


def test_job_reviewed_rejection_preserved(tmp_path: Path) -> None:
    service = _service(
        [
            "weak",
            _review_json("needs_revision"),
            "still weak",
            _review_json("needs_revision"),
        ]
    )
    runtime = _runtime(tmp_path, service)
    app = create_app(
        api_settings=_settings(),
        service=service,
        job_runtime=runtime,
    )
    with TestClient(app) as client:
        submit = client.post(
            "/v1/jobs",
            headers=AUTH,
            json={
                "api_version": "v1",
                "plan": _simple_plan().model_dump(mode="json"),
                "review_enabled": True,
                "max_revisions": 1,
            },
        )
        assert submit.status_code == 202
        job_id = submit.json()["job_id"]
        result = client.get(f"/v1/jobs/{job_id}/result", headers=AUTH).json()
        task = result["result"]["execution"]["task_results"][0]
        assert task["status"] == TaskStatus.REJECTED.value
        assert task["quality_approved"] is False
        assert task["review_policy"] == "reviewed"
        assert len(task["attempts"]) == 2


def test_job_endpoints_require_auth(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _service(["ok"]))
    app = create_app(api_settings=_settings(), job_runtime=runtime)
    with TestClient(app) as client:
        assert (
            client.post(
                "/v1/jobs",
                json={"api_version": "v1", "plan": _simple_plan().model_dump()},
            ).status_code
            == 401
        )
        assert client.get("/v1/jobs/x").status_code == 401
        assert client.get("/v1/jobs/x/result").status_code == 401


def test_invalid_job_request_rejected(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _service())
    app = create_app(api_settings=_settings(), job_runtime=runtime)
    with TestClient(app) as client:
        response = client.post(
            "/v1/jobs",
            headers=AUTH,
            json={"api_version": "v1"},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_sync_runs_still_works(tmp_path: Path) -> None:
    service = _service(["sync-ok"])
    runtime = _runtime(tmp_path, service)
    app = create_app(api_settings=_settings(), service=service, job_runtime=runtime)
    with TestClient(app) as client:
        response = client.post(
            "/v1/runs",
            headers=AUTH,
            json={
                "api_version": "v1",
                "plan": _simple_plan().model_dump(mode="json"),
            },
        )
    assert response.status_code == 200
    assert response.json()["execution"]["task_results"][0]["output"] == "sync-ok"


def test_duplicate_delivery_does_not_run_concurrently(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    llm = BlockingLLM(entered, release)

    @contextmanager
    def scope() -> Iterator[LLMClient]:
        yield llm

    service = OrchestrationService(client_scope=scope)
    # Non-immediate: enqueue only; we drive handle_delivery ourselves.
    runtime = _runtime(tmp_path, service, immediate=False)
    request = RunRequest(api_version="v1", plan=_simple_plan())
    record = runtime.store.create_queued_job(
        request.model_dump(mode="json"),
        max_attempts=3,
    )

    results: list[str] = []

    def run_delivery() -> None:
        runtime.handle_delivery(record.job_id)
        results.append("done")

    t1 = threading.Thread(target=run_delivery)
    t1.start()
    assert entered.wait(timeout=2)
    # Second delivery while first holds lease — must be ignored.
    runtime.handle_delivery(record.job_id)
    assert llm.calls == 1
    release.set()
    t1.join(timeout=2)
    assert results == ["done"]
    final = runtime.store.get(record.job_id)
    assert final.status is JobStatus.SUCCEEDED
    assert final.attempt == 1


def test_lease_expiry_and_restart_recovery(tmp_path: Path) -> None:
    service = _service(["recovered"])
    runtime = _runtime(tmp_path, service, immediate=False, lease_ms=50.0)
    request = RunRequest(api_version="v1", plan=_simple_plan())
    record = runtime.store.create_queued_job(
        request.model_dump(mode="json"),
        max_attempts=2,
    )
    # Simulate a crashed worker that claimed then vanished.
    claimed = runtime.store.try_claim(
        record.job_id,
        owner_id="dead-worker",
        lease_ms=50.0,
    )
    assert claimed is not None
    assert claimed.status is JobStatus.RUNNING
    time.sleep(0.08)
    recovered = runtime.store.recover_expired_leases()
    assert record.job_id in recovered
    interrupted = runtime.store.get(record.job_id)
    assert interrupted.status is JobStatus.INTERRUPTED
    assert interrupted.uncertainty

    # New worker delivery succeeds.
    set_runtime(runtime)
    runtime.handle_delivery(record.job_id)
    final = runtime.store.get(record.job_id)
    assert final.status is JobStatus.SUCCEEDED
    assert final.attempt == 2
    assert final.result is not None


def test_bounded_infrastructure_retries(tmp_path: Path) -> None:
    service = _service(["never"])
    runtime = _runtime(
        tmp_path,
        service,
        immediate=False,
        max_attempts=1,
        lease_ms=30.0,
    )
    request = RunRequest(api_version="v1", plan=_simple_plan())
    record = runtime.store.create_queued_job(
        request.model_dump(mode="json"),
        max_attempts=1,
    )
    runtime.store.try_claim(
        record.job_id,
        owner_id="dead",
        lease_ms=30.0,
    )
    time.sleep(0.05)
    # First recovery → interrupted (attempt already 1 == max).
    # With attempt>=max_attempts on expiry, store marks failed.
    recovered = runtime.store.recover_expired_leases()
    final = runtime.store.get(record.job_id)
    # attempt was 1 and max_attempts=1 → failed on expiry
    assert final.status is JobStatus.FAILED
    assert final.error_code == "retry_budget_exhausted"
    assert recovered == []


def test_worker_process_capacity_defers_without_claim(tmp_path: Path) -> None:
    service = _service(["a", "b"])
    runtime = _runtime(
        tmp_path,
        service,
        immediate=False,
        max_runs=1,
        max_tasks=1,
    )
    request = RunRequest(
        api_version="v1",
        plan=_simple_plan(),
        max_concurrency=1,
    )
    job_a = runtime.store.create_queued_job(
        request.model_dump(mode="json"),
        max_attempts=2,
    )
    job_b = runtime.store.create_queued_job(
        request.model_dump(mode="json"),
        max_attempts=2,
    )
    entered = threading.Event()
    release = threading.Event()
    blocking = BlockingLLM(entered, release)

    @contextmanager
    def scope() -> Iterator[LLMClient]:
        yield blocking

    runtime.service = OrchestrationService(client_scope=scope)

    t = threading.Thread(target=runtime.handle_delivery, args=(job_a.job_id,))
    t.start()
    assert entered.wait(timeout=2)
    # Second job should defer: capacity full, attempt stays 0.
    runtime.handle_delivery(job_b.job_id)
    b = runtime.store.get(job_b.job_id)
    assert b.status is JobStatus.QUEUED
    assert b.attempt == 0
    release.set()
    t.join(timeout=2)
    runtime.service = service
    runtime.handle_delivery(job_b.job_id)
    assert runtime.store.get(job_b.job_id).status is JobStatus.SUCCEEDED


def test_jobs_unavailable_returns_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _runtime(tmp_path, _service(["ok"]))
    app = create_app(api_settings=_settings(), job_runtime=runtime)

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(runtime, "submit", boom)
    with TestClient(app) as client:
        response = client.post(
            "/v1/jobs",
            headers=AUTH,
            json={
                "api_version": "v1",
                "plan": _simple_plan().model_dump(mode="json"),
            },
        )
    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "jobs_unavailable"


def test_result_not_ready_returns_409(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _service(["ok"]), immediate=False)
    app = create_app(api_settings=_settings(), job_runtime=runtime)
    request = RunRequest(api_version="v1", plan=_simple_plan())
    record = runtime.store.create_queued_job(
        request.model_dump(mode="json"),
        max_attempts=2,
    )
    with TestClient(app) as client:
        response = client.get(f"/v1/jobs/{record.job_id}/result", headers=AUTH)
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "job_not_ready"


def test_durable_create_survives_enqueue_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(["after-recovery"])
    runtime = _runtime(tmp_path, service, immediate=False)
    request = RunRequest(api_version="v1", plan=_simple_plan())

    def fail_enqueue(_job_id: str) -> None:
        raise RuntimeError("queue down")

    monkeypatch.setattr(runtime.queue, "enqueue", fail_enqueue)
    record = runtime.submit(request)
    assert record.status is JobStatus.QUEUED
    assert runtime.store.get(record.job_id).status is JobStatus.QUEUED

    monkeypatch.undo()
    # Recovery re-enqueues; drive delivery directly.
    pending = runtime.store.list_needs_delivery()
    assert record.job_id in pending
    set_runtime(runtime)
    runtime.handle_delivery(record.job_id)
    assert runtime.store.get(record.job_id).status is JobStatus.SUCCEEDED


def test_sqlite_huey_integration_non_immediate(tmp_path: Path) -> None:
    """Real local SQLite job store + SqliteHuey delivery (consumer-driven)."""

    service = _service(["integration-ok"])
    store = JobStore(tmp_path / "jobs_int.db")
    queue = JobQueue(tmp_path / "huey_int.db", immediate=False)
    capacity = CapacityGate(max_runs=2, max_tasks=8)
    runtime = JobRuntime(
        store=store,
        queue=queue,
        service=service,
        capacity=capacity,
        max_run_concurrency=4,
        max_job_attempts=2,
        lease_ms=30_000,
        worker_id="integration-worker",
    )
    set_runtime(runtime)
    request = RunRequest(api_version="v1", plan=_simple_plan())
    record = runtime.submit(request)
    assert record.status is JobStatus.QUEUED

    # Drain one message the same way a Huey worker would.
    task = queue.huey.dequeue()
    assert task is not None
    queue.huey.execute(task)

    final = store.get(record.job_id)
    assert final.status is JobStatus.SUCCEEDED
    assert final.result["execution"]["task_results"][0]["output"] == "integration-ok"
    assert final.timing.execute_ms is not None
    assert final.timing.total_ms is not None


def test_client_job_helpers_over_local_http(tmp_path: Path) -> None:
    import socket

    import uvicorn

    service = _service(["client-ok"])
    runtime = _runtime(tmp_path, service)
    app = create_app(api_settings=_settings(), service=service, job_runtime=runtime)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=1.0) as probe:
                if probe.get("/health").status_code == 200:
                    break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        raise AssertionError("uvicorn server did not start")
    try:
        with OrchestrationClient(
            f"http://127.0.0.1:{port}",
            token="test-token",
            connect_timeout_seconds=1.0,
            read_timeout_seconds=10.0,
        ) as client:
            submitted = client.submit_job(
                RunRequest(api_version="v1", plan=_simple_plan())
            )
            status = client.wait_for_job(submitted.job_id, timeout_seconds=5)
            assert status.status is JobStatus.SUCCEEDED
            result = client.get_job_result(submitted.job_id)
            assert result.result is not None
            assert (
                result.result["execution"]["task_results"][0]["output"] == "client-ok"
            )
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)

