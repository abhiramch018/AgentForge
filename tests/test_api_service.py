"""API auth, capacity, review outcomes, and HTTP client tests with fake LLMs."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.client import (
    OrchestrationAPIError,
    OrchestrationClient,
    OrchestrationTransportError,
)
from app.api.config import APISettings
from app.api.main import create_app
from app.api.service import OrchestrationService
from app.api.v1.schemas import PlanInput, RunRequest
from app.core.messages import ChatMessage
from app.llm.base import LLMClient, LLMResponse
from app.orchestration import SpecialistRole, TaskStatus


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
    }
    base.update(kwargs)
    return APISettings(_env_file=None, **base)  # type: ignore[arg-type]


def _service(answers: Sequence[str] | None = None) -> OrchestrationService:
    llm = ScriptedLLM(answers)

    @contextmanager
    def scope() -> Iterator[LLMClient]:
        yield llm

    return OrchestrationService(client_scope=scope)


def _plan(*task_specs: dict[str, object]) -> PlanInput:
    return PlanInput(goal="Demo goal", tasks=list(task_specs))  # type: ignore[arg-type]


def _simple_plan() -> PlanInput:
    return _plan(
        {
            "id": "only",
            "description": "Do the task",
            "role": "general",
            "depends_on": [],
            "expected_output": "A short answer",
        }
    )


def test_health_is_public() -> None:
    app = create_app(api_settings=_settings(), service=_service())
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_meta_and_runs_require_bearer_token() -> None:
    app = create_app(api_settings=_settings(), service=_service(["ok"]))
    with TestClient(app) as client:
        assert client.get("/v1/meta").status_code == 401
        assert (
            client.post("/v1/runs", json={"api_version": "v1", "goal": "x"}).status_code
            == 401
        )
        headers = {"Authorization": "Bearer test-token"}
        meta = client.get("/v1/meta", headers=headers)
        assert meta.status_code == 200
        assert meta.json()["auth_required"] is True
        assert meta.json()["tools_enabled"] is False
        assert meta.json()["capacity_scope"] == "single_process"


def test_unauthenticated_local_mode() -> None:
    settings = _settings(api_token=None, allow_unauthenticated_local=True)
    app = create_app(api_settings=settings, service=_service(["ok"]))
    with TestClient(app) as client:
        assert client.get("/v1/meta").status_code == 200
        response = client.post(
            "/v1/runs",
            json={"api_version": "v1", "plan": _simple_plan().model_dump(mode="json")},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["execution"]["task_results"][0]["quality_approved"] is False
    assert (
        body["execution"]["task_results"][0]["review_policy"]
        == "bypassed_review_disabled"
    )


def test_invalid_request_rejected() -> None:
    app = create_app(api_settings=_settings(), service=_service())
    with TestClient(app) as client:
        response = client.post(
            "/v1/runs",
            headers={"Authorization": "Bearer test-token"},
            json={"api_version": "v1"},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_successful_run_preserves_review_bypass_fields() -> None:
    app = create_app(api_settings=_settings(), service=_service(["done"]))
    with TestClient(app) as http:
        response = http.post(
            "/v1/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "api_version": "v1",
                "plan": _simple_plan().model_dump(mode="json"),
            },
        )
    assert response.status_code == 200
    body = response.json()
    task = body["execution"]["task_results"][0]
    assert task["status"] == "succeeded"
    assert task["quality_approved"] is False
    assert task["review_bypass_reason"] == "review_disabled"
    assert body["effective_max_concurrency"] == 4


def test_review_acceptance_and_rejection_over_http() -> None:
    accept_app = create_app(
        api_settings=_settings(),
        service=_service(["draft", _review_json("accepted")]),
    )
    with TestClient(accept_app) as http:
        accepted = http.post(
            "/v1/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "api_version": "v1",
                "plan": _simple_plan().model_dump(mode="json"),
                "review_enabled": True,
                "max_revisions": 1,
            },
        )
    assert accepted.status_code == 200
    task = accepted.json()["execution"]["task_results"][0]
    assert task["quality_approved"] is True
    assert task["review_policy"] == "reviewed"

    reject_app = create_app(
        api_settings=_settings(),
        service=_service(
            [
                "weak",
                _review_json("needs_revision"),
                "still weak",
                _review_json("needs_revision"),
            ]
        ),
    )
    with TestClient(reject_app) as http:
        rejected = http.post(
            "/v1/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "api_version": "v1",
                "plan": _plan(
                    {
                        "id": "write",
                        "description": "Write",
                        "role": "writer",
                        "depends_on": [],
                        "expected_output": "Good text",
                    },
                    {
                        "id": "next",
                        "description": "Follow",
                        "role": "general",
                        "depends_on": ["write"],
                        "expected_output": "Next",
                    },
                ).model_dump(mode="json"),
                "review_enabled": True,
                "max_revisions": 1,
            },
        )
    assert rejected.status_code == 200
    by_id = {t["task_id"]: t for t in rejected.json()["execution"]["task_results"]}
    assert by_id["write"]["status"] == "rejected"
    assert by_id["next"]["status"] == "skipped"
    assert len(by_id["write"]["attempts"]) == 2


def test_server_clamps_requested_concurrency() -> None:
    app = create_app(
        api_settings=_settings(max_run_concurrency=2),
        service=_service(["a", "b"]),
    )
    with TestClient(app) as http:
        response = http.post(
            "/v1/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "api_version": "v1",
                "plan": _plan(
                    {
                        "id": "t1",
                        "description": "A",
                        "role": "researcher",
                        "depends_on": [],
                        "expected_output": "A",
                    },
                    {
                        "id": "t2",
                        "description": "B",
                        "role": "analyst",
                        "depends_on": [],
                        "expected_output": "B",
                    },
                ).model_dump(mode="json"),
                "max_concurrency": 32,
            },
        )
    assert response.status_code == 200
    assert response.json()["effective_max_concurrency"] == 2


def test_capacity_rejects_when_full() -> None:
    settings = _settings(
        max_inflight_runs=1,
        max_inflight_tasks=4,
        max_run_concurrency=1,
    )
    app = create_app(api_settings=settings, service=_service(["ok"]))
    gate = app.state.capacity_gate
    with gate.reserve(1):
        with TestClient(app) as http:
            response = http.post(
                "/v1/runs",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "api_version": "v1",
                    "plan": _simple_plan().model_dump(mode="json"),
                    "max_concurrency": 1,
                },
            )
    assert response.status_code == 429
    assert response.json()["detail"]["error"]["code"] == "capacity_exceeded"


def test_capacity_releases_after_internal_failure() -> None:
    class BoomService(OrchestrationService):
        def run(self, request, *, run_id, concurrency):  # noqa: ANN001
            raise RuntimeError("boom")

    app = create_app(
        api_settings=_settings(max_inflight_runs=1, max_inflight_tasks=4),
        service=BoomService(),
    )
    with TestClient(app) as http:
        headers = {"Authorization": "Bearer test-token"}
        body = {
            "api_version": "v1",
            "plan": _simple_plan().model_dump(mode="json"),
            "max_concurrency": 1,
        }
        first = http.post("/v1/runs", headers=headers, json=body)
        assert first.status_code == 500
        assert first.json()["detail"]["error"]["code"] == "internal_error"
        # Capacity must be free for the next admission.
        assert app.state.capacity_gate.snapshot()["active_runs"] == 0
        second = http.post("/v1/runs", headers=headers, json=body)
        assert second.status_code == 500


def test_client_timeout_is_uncertain_and_not_retried() -> None:
    calls = {"n": 0}

    class TimeoutTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            raise httpx.ReadTimeout("slow", request=request)

    with OrchestrationClient(
        "http://example.invalid",
        token="test-token",
        transport=TimeoutTransport(),
        connect_timeout_seconds=0.5,
        read_timeout_seconds=0.01,
    ) as client:
        with pytest.raises(OrchestrationTransportError, match="uncertain"):
            client.create_run(RunRequest(plan=_simple_plan()))
    assert calls["n"] == 1


def test_real_local_http_with_uvicorn_and_fake_llm() -> None:
    import socket
    import threading

    import uvicorn

    app = create_app(
        api_settings=_settings(allow_unauthenticated_local=False),
        service=_service(["http-ok"]),
    )
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
            result = client.create_run(RunRequest(plan=_simple_plan()))
        assert result.execution.task_results[0].output == "http-ok"
        assert result.execution.task_results[0].quality_approved is False
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
