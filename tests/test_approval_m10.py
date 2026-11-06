"""M10 human-in-the-loop approval (fake LLMs, no shell)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.concurrency import CapacityGate
from app.api.config import APISettings
from app.api.jobs.models import JobStatus
from app.api.jobs.queue import JobQueue
from app.api.jobs.runtime import JobRuntime, set_runtime
from app.api.jobs.store import JobStore
from app.api.main import create_app
from app.api.service import OrchestrationService
from app.api.v1.schemas import PlanInput, RunRequest
from app.approval.actions import execute_write_artifact
from app.approval.exceptions import ApprovalRequired
from app.approval.models import ApprovalStatus, ProtectedAction, ProposalFingerprint
from app.approval.service import ApprovalService
from app.approval.store import ApprovalStore
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
        content = self._answers.pop(0) if self._answers else "draft"
        return LLMResponse(content=content, model="fake-model")


def _settings(**kwargs: object) -> APISettings:
    base = {
        "api_token": "test-token",
        "allow_unauthenticated_local": False,
        "max_inflight_runs": 2,
        "max_inflight_tasks": 8,
        "max_run_concurrency": 4,
        "workspace_id": "ws-hitl",
        "jobs_immediate": True,
        "max_job_attempts": 2,
    }
    base.update(kwargs)
    return APISettings(_env_file=None, **base)  # type: ignore[arg-type]


def _plan() -> PlanInput:
    return PlanInput(
        goal="Prepare release note",
        tasks=[
            {
                "id": "draft",
                "description": "Draft the note",
                "role": "general",
                "depends_on": [],
                "expected_output": "Short draft",
            }
        ],
    )


def _action(path: str = "notes/release.txt", content: str = "ship it") -> ProtectedAction:
    return ProtectedAction(
        relative_path=path,
        content=content,
        reason="Publish release note artifact",
    )


def _service(
    tmp_path: Path,
    answers: Sequence[str] | None = None,
) -> tuple[OrchestrationService, ApprovalService, JobRuntime]:
    llm = ScriptedLLM(answers)

    @contextmanager
    def scope() -> Iterator[LLMClient]:
        yield llm

    approvals = ApprovalService(
        ApprovalStore(tmp_path / "approvals.db"),
        workspace_id="ws-hitl",
        artifact_root=str(tmp_path / "artifacts"),
        ttl_ms=3_600_000,
    )
    service = OrchestrationService(
        client_scope=scope,
        memory=NullMemoryService(),
        approval=approvals,
    )
    store = JobStore(tmp_path / "jobs.db")
    queue = JobQueue(tmp_path / "huey.db", immediate=True)
    runtime = JobRuntime(
        store=store,
        queue=queue,
        service=service,
        capacity=CapacityGate(max_runs=2, max_tasks=8),
        max_run_concurrency=4,
        max_job_attempts=2,
        lease_ms=30_000,
        approval=approvals,
        worker_id="test-worker",
    )
    set_runtime(runtime)
    return service, approvals, runtime


AUTH = {"Authorization": "Bearer test-token"}


def test_no_artifact_before_approval(tmp_path: Path) -> None:
    _, approvals, runtime = _service(tmp_path, ["draft text"])
    request = RunRequest(
        api_version="v1",
        plan=_plan(),
        memory_enabled=False,
        protected_action=_action(),
    )
    record = runtime.submit(request)
    assert record.status is JobStatus.AWAITING_APPROVAL
    assert not (tmp_path / "artifacts" / "notes" / "release.txt").exists()
    pending = approvals.list_pending()
    assert len(pending) == 1
    assert pending[0].fingerprint


def test_reject_prevents_artifact(tmp_path: Path) -> None:
    _, approvals, runtime = _service(tmp_path, ["draft"])
    request = RunRequest(
        api_version="v1",
        plan=_plan(),
        memory_enabled=False,
        protected_action=_action(),
    )
    job = runtime.submit(request)
    pending = approvals.list_pending()[0]
    approvals.reject(pending.approval_id, note="nope")
    runtime.store.mark_failed_from_any(
        job.job_id,
        error_code="approval_rejected",
        error_message="nope",
    )
    assert not (tmp_path / "artifacts" / "notes" / "release.txt").exists()
    assert runtime.store.get(job.job_id).status is JobStatus.FAILED


def test_approve_then_resume_writes_artifact(tmp_path: Path) -> None:
    _, approvals, runtime = _service(tmp_path, ["draft"])
    action = _action(content="approved content")
    request = RunRequest(
        api_version="v1",
        plan=_plan(),
        memory_enabled=False,
        protected_action=action,
    )
    job = runtime.submit(request)
    assert job.status is JobStatus.AWAITING_APPROVAL
    pending = approvals.list_pending()[0]
    approvals.approve(pending.approval_id, fingerprint=pending.fingerprint)
    resumed = runtime.resume_after_approval(
        job.job_id, fingerprint=pending.fingerprint
    )
    final = runtime.store.get(resumed.job_id)
    assert final.status is JobStatus.SUCCEEDED
    path = tmp_path / "artifacts" / "notes" / "release.txt"
    assert path.read_text(encoding="utf-8") == "approved content"
    assert final.result is not None
    assert final.result["artifact"]["bytes_written"] > 0


def test_changed_proposal_needs_reapproval(tmp_path: Path) -> None:
    _, approvals, runtime = _service(tmp_path, ["draft"])
    original = _action(content="v1")
    job = runtime.submit(
        RunRequest(
            api_version="v1",
            plan=_plan(),
            memory_enabled=False,
            protected_action=original,
        )
    )
    pending = approvals.list_pending()[0]
    revised_action = _action(content="v2-changed")
    new_pending = approvals.revise(pending.approval_id, action=revised_action)
    assert new_pending.status is ApprovalStatus.PENDING
    assert new_pending.fingerprint != pending.fingerprint
    assert approvals.get(pending.approval_id).status is ApprovalStatus.SUPERSEDED

    # Old fingerprint must not authorize the new proposal.
    with pytest.raises(Exception):
        approvals.approve(
            new_pending.approval_id,
            fingerprint=pending.fingerprint,
        )
    approvals.approve(
        new_pending.approval_id,
        fingerprint=new_pending.fingerprint,
    )
    # Sync checkpoint like the API would.
    checkpoint = dict(runtime.store.get(job.job_id).checkpoint or {})
    checkpoint["action"] = revised_action.model_dump(mode="json")
    checkpoint["fingerprint"] = new_pending.fingerprint
    runtime.store.update_checkpoint(
        job.job_id,
        checkpoint=checkpoint,
        expected_status=JobStatus.AWAITING_APPROVAL,
    )
    runtime.resume_after_approval(
        job.job_id, fingerprint=new_pending.fingerprint
    )
    assert (tmp_path / "artifacts" / "notes" / "release.txt").read_text(
        encoding="utf-8"
    ) == "v2-changed"


def test_awaiting_approval_survives_restart_without_capacity(
    tmp_path: Path,
) -> None:
    _, approvals, runtime = _service(tmp_path, ["draft"])
    job = runtime.submit(
        RunRequest(
            api_version="v1",
            plan=_plan(),
            memory_enabled=False,
            protected_action=_action(),
        )
    )
    assert job.status is JobStatus.AWAITING_APPROVAL
    assert runtime.capacity.snapshot()["active_runs"] == 0
    # Simulate restart: recover must not re-enqueue awaiting jobs.
    recovered = runtime.recover()
    assert job.job_id not in recovered
    assert runtime.store.get(job.job_id).status is JobStatus.AWAITING_APPROVAL
    assert approvals.list_pending()


def test_duplicate_delivery_while_awaiting_is_ignored(tmp_path: Path) -> None:
    _, _, runtime = _service(tmp_path, ["draft"])
    job = runtime.submit(
        RunRequest(
            api_version="v1",
            plan=_plan(),
            memory_enabled=False,
            protected_action=_action(),
        )
    )
    runtime.handle_delivery(job.job_id)
    assert runtime.store.get(job.job_id).status is JobStatus.AWAITING_APPROVAL
    assert runtime.capacity.snapshot()["active_runs"] == 0


def test_duplicate_approve_is_conflict(tmp_path: Path) -> None:
    _, approvals, runtime = _service(tmp_path, ["draft"])
    runtime.submit(
        RunRequest(
            api_version="v1",
            plan=_plan(),
            memory_enabled=False,
            protected_action=_action(),
        )
    )
    pending = approvals.list_pending()[0]
    approvals.approve(pending.approval_id, fingerprint=pending.fingerprint)
    with pytest.raises(Exception):
        approvals.approve(pending.approval_id, fingerprint=pending.fingerprint)


def test_idempotent_artifact_replay(tmp_path: Path) -> None:
    action = _action(content="same")
    root = tmp_path / "artifacts"
    first = execute_write_artifact(action, output_root=root)
    second = execute_write_artifact(action, output_root=root)
    assert first.idempotent_replay is False
    assert second.idempotent_replay is True


def test_sync_runs_reject_protected_action(tmp_path: Path) -> None:
    _, approvals, runtime = _service(tmp_path, ["x"])
    app = create_app(
        api_settings=_settings(
            jobs_db_path=str(tmp_path / "jobs.db"),
            approvals_db_path=str(tmp_path / "approvals.db"),
            artifact_output_root=str(tmp_path / "artifacts"),
        ),
        service=runtime.service,
        job_runtime=runtime,
        approval_service=approvals,
        memory_service=NullMemoryService(),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/runs",
            headers=AUTH,
            json={
                "api_version": "v1",
                "plan": _plan().model_dump(mode="json"),
                "memory_enabled": False,
                "protected_action": _action().model_dump(mode="json"),
            },
        )
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "approval_requires_jobs"


def test_approval_http_flow(tmp_path: Path) -> None:
    _, approvals, runtime = _service(tmp_path, ["http-draft"])
    app = create_app(
        api_settings=_settings(
            jobs_db_path=str(tmp_path / "jobs.db"),
            queue_db_path=str(tmp_path / "huey.db"),
            approvals_db_path=str(tmp_path / "approvals.db"),
            artifact_output_root=str(tmp_path / "artifacts"),
            jobs_immediate=True,
        ),
        service=runtime.service,
        job_runtime=runtime,
        approval_service=approvals,
        memory_service=NullMemoryService(),
    )
    with TestClient(app) as client:
        submit = client.post(
            "/v1/jobs",
            headers=AUTH,
            json={
                "api_version": "v1",
                "plan": _plan().model_dump(mode="json"),
                "memory_enabled": False,
                "protected_action": _action(content="via-http").model_dump(
                    mode="json"
                ),
            },
        )
        assert submit.status_code == 202
        job_id = submit.json()["job_id"]
        status = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
        assert status["status"] == "awaiting_approval"
        listed = client.get("/v1/approvals", headers=AUTH).json()
        assert listed["count"] == 1
        approval = listed["items"][0]
        approved = client.post(
            f"/v1/approvals/{approval['approval_id']}/approve",
            headers=AUTH,
            json={"fingerprint": approval["fingerprint"]},
        )
        assert approved.status_code == 200
        final = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
        assert final["status"] == "succeeded"
        assert (tmp_path / "artifacts" / "notes" / "release.txt").read_text(
            encoding="utf-8"
        ) == "via-http"


def test_fingerprint_binds_job_and_action() -> None:
    a = ProposalFingerprint.of(_action(content="a"), job_id="j1", task_id="t")
    b = ProposalFingerprint.of(_action(content="b"), job_id="j1", task_id="t")
    c = ProposalFingerprint.of(_action(content="a"), job_id="j2", task_id="t")
    assert a != b
    assert a != c


def test_gate_raises_without_executing(tmp_path: Path) -> None:
    approvals = ApprovalService(
        ApprovalStore(tmp_path / "a.db"),
        workspace_id="ws",
        artifact_root=str(tmp_path / "out"),
    )
    action = _action()
    with pytest.raises(ApprovalRequired) as exc:
        approvals.request_or_resume(
            job_id="job-1",
            action=action,
            checkpoint={"phase": "awaiting_protected_action"},
        )
    assert not list((tmp_path / "out").glob("**/*")) if (tmp_path / "out").exists() else True
    assert exc.value.approval_id
