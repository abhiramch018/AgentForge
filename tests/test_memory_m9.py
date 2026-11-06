"""M9 memory: store, retrieval, workspace scope, APIs (fake embeddings/LLMs)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.config import APISettings
from app.api.main import create_app
from app.api.service import OrchestrationService
from app.api.v1.schemas import PlanInput, RunRequest
from app.core.messages import ChatMessage
from app.llm.base import LLMClient, LLMResponse
from app.memory.embeddings import CachingEmbedder, HashingEmbedder, cosine_similarity
from app.memory.models import MemoryKind, MemoryTrust
from app.memory.service import MemoryService, NullMemoryService, format_memory_reference
from app.memory.store import MemoryStore
from app.orchestration.execution import (
    ExecutionResult,
    ExecutionStatus,
    TaskResult,
    TaskStatus,
)
from app.orchestration.review import ReviewPolicy


class ScriptedLLM(LLMClient):
    def __init__(self, answers: Sequence[str] | None = None) -> None:
        self._answers = list(answers or [])
        self.calls = 0
        self.last_user: str | None = None

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        self.calls += 1
        for msg in reversed(messages):
            if msg.role.value == "user":
                self.last_user = msg.content
                break
        if self._answers:
            content = self._answers.pop(0)
        else:
            content = "specialist output"
        return LLMResponse(content=content, model="fake-model")


def _settings(**kwargs: object) -> APISettings:
    base = {
        "api_token": "test-token",
        "allow_unauthenticated_local": False,
        "max_inflight_runs": 2,
        "max_inflight_tasks": 8,
        "max_run_concurrency": 4,
        "workspace_id": "ws-test",
        "memory_retention_days": 30.0,
    }
    base.update(kwargs)
    return APISettings(_env_file=None, **base)  # type: ignore[arg-type]


def _service(
    answers: Sequence[str] | None = None,
    *,
    memory: MemoryService | NullMemoryService | None = None,
) -> OrchestrationService:
    llm = ScriptedLLM(answers)

    @contextmanager
    def scope() -> Iterator[LLMClient]:
        yield llm

    return OrchestrationService(client_scope=scope, memory=memory)


def _simple_plan(goal: str = "Demo goal") -> PlanInput:
    return PlanInput(
        goal=goal,
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


def _task(
    *,
    status: TaskStatus = TaskStatus.SUCCEEDED,
    output: str | None = "useful answer about microservices",
    quality_approved: bool = False,
    error: str | None = None,
) -> TaskResult:
    return TaskResult(
        task_id="only",
        role="general",
        status=status,
        output=output,
        error=error,
        quality_approved=quality_approved,
        review_policy=(
            ReviewPolicy.REVIEWED
            if quality_approved
            else ReviewPolicy.BYPASSED_REVIEW_DISABLED
        ),
        review_bypass_reason=None if quality_approved else "review_disabled",
    )


def _execution(*tasks: TaskResult, goal: str = "Demo goal") -> ExecutionResult:
    return ExecutionResult(
        goal=goal,
        status=ExecutionStatus.SUCCEEDED,
        task_results=list(tasks),
        duration_ms=1.0,
        succeeded_count=sum(1 for t in tasks if t.status is TaskStatus.SUCCEEDED),
        failed_count=sum(1 for t in tasks if t.status is TaskStatus.FAILED),
        timed_out_count=0,
        skipped_count=0,
        rejected_count=sum(1 for t in tasks if t.status is TaskStatus.REJECTED),
    )


AUTH = {"Authorization": "Bearer test-token"}


def test_hashing_embedder_is_deterministic() -> None:
    emb = HashingEmbedder(dimensions=32)
    a = emb.embed("microservices latency budget")
    b = emb.embed("microservices latency budget")
    assert a == b
    assert cosine_similarity(a, a) == pytest.approx(1.0)


def test_caching_embedder_avoids_repeat_work() -> None:
    inner = HashingEmbedder(dimensions=32)
    cached = CachingEmbedder(inner)
    cached.embed("same text")
    cached.embed("same text")
    assert cached.hits == 1
    assert cached.misses == 1


def test_persist_and_retrieve_relevant(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.db")
    svc = MemoryService(store, workspace_id="ws-a", retention_days=30)
    write = svc.remember_execution(
        run_id="r1",
        goal="Explain microservices tradeoffs",
        execution=_execution(
            _task(output="Microservices isolate deploy units but add network cost."),
            goal="Explain microservices tradeoffs",
        ),
    )
    assert write.written_count == 1
    items, block, metrics = svc.retrieve(
        "microservices deployment tradeoffs",
        top_k=3,
        max_context_chars=2000,
    )
    assert metrics.returned_count >= 1
    assert metrics.retrieval_ms is not None
    assert "untrusted" in block.lower()
    assert items[0].trust is MemoryTrust.UNREVIEWED
    assert "Microservices isolate" in items[0].summary


def test_reviewed_vs_failed_trust_labels(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.db")
    svc = MemoryService(store, workspace_id="ws-a")
    svc.remember_execution(
        run_id="r1",
        goal="Write docs",
        execution=_execution(
            _task(output="Accepted docs draft", quality_approved=True),
            goal="Write docs",
        ),
    )
    svc.remember_execution(
        run_id="r2",
        goal="Write docs",
        execution=_execution(
            _task(
                status=TaskStatus.REJECTED,
                output=None,
                error="revision budget exhausted",
            ),
            goal="Write docs",
        ),
    )
    listed = svc.list_memories()
    trusts = {m.trust for m in listed}
    assert MemoryTrust.REVIEWED in trusts
    assert MemoryTrust.FAILED_APPROACH in trusts


def test_workspace_filtering(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.db")
    a = MemoryService(store, workspace_id="alpha")
    b = MemoryService(store, workspace_id="beta")
    a.remember_execution(
        run_id="r1",
        goal="alpha secret project",
        execution=_execution(
            _task(output="alpha-only payload about widgets"),
            goal="alpha secret project",
        ),
    )
    items, _, _ = b.retrieve("widgets", top_k=5)
    assert items == []
    assert a.list_memories()
    assert b.list_memories() == []


def test_duplicate_write_skipped(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.db")
    svc = MemoryService(store, workspace_id="ws")
    exec_result = _execution(
        _task(output="identical summary text"),
        goal="goal",
    )
    first = svc.remember_execution(run_id="r1", goal="goal", execution=exec_result)
    second = svc.remember_execution(run_id="r2", goal="goal", execution=exec_result)
    assert first.written_count == 1
    assert second.skipped_duplicates == 1
    assert len(svc.list_memories()) == 1


def test_stale_entries_purged_by_retention(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.db")
    svc = MemoryService(store, workspace_id="ws", retention_days=0.000001)
    # Insert directly with old timestamp via store
    emb = HashingEmbedder().embed("old")
    store.insert(
        workspace_id="ws",
        kind=MemoryKind.TASK_SUMMARY,
        trust=MemoryTrust.UNREVIEWED,
        summary="old memory about cats",
        provenance={"run_id": "old"},
        content_hash="hash-old",
        embedding=emb,
    )
    # Force created_at into the past
    import sqlite3

    conn = sqlite3.connect(store.path)
    conn.execute(
        "UPDATE memories SET created_at_ms = ? WHERE content_hash = ?",
        (time.time() * 1000 - 86_400_000.0, "hash-old"),
    )
    conn.commit()
    conn.close()
    svc.list_memories()  # applies retention
    assert svc.list_memories() == []


def test_deletion_clears_vectors_and_retrieval(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.db")
    svc = MemoryService(store, workspace_id="ws")
    svc.remember_execution(
        run_id="r1",
        goal="delete me topic",
        execution=_execution(
            _task(output="deletable memory content about orchids"),
            goal="delete me topic",
        ),
    )
    mid = svc.list_memories()[0].memory_id
    svc.delete(mid)
    items, _, metrics = svc.retrieve("orchids", top_k=5)
    assert items == []
    assert metrics.returned_count == 0
    # Soft-deleted row has empty embedding
    import sqlite3

    conn = sqlite3.connect(store.path)
    row = conn.execute(
        "SELECT embedding_json, deleted_at_ms FROM memories WHERE memory_id = ?",
        (mid,),
    ).fetchone()
    conn.close()
    assert row[1] is not None
    assert json.loads(row[0]) == []


def test_memory_disabled_skips_retrieve_and_write(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.db")
    memory = MemoryService(store, workspace_id="ws")
    llm = ScriptedLLM(["first-run-answer"])

    @contextmanager
    def scope() -> Iterator[LLMClient]:
        yield llm

    service = OrchestrationService(client_scope=scope, memory=memory)
    response = service.run(
        RunRequest(
            api_version="v1",
            plan=_simple_plan("memory off"),
            memory_enabled=False,
        ),
        run_id="r-off",
        concurrency=1,
    )
    assert response.memory_enabled is False
    assert response.timing.memory is not None
    assert response.timing.memory.enabled is False
    assert memory.list_memories() == []
    assert llm.last_user is not None
    assert "<memory" not in llm.last_user


def test_unavailable_memory_is_graceful(tmp_path: Path) -> None:
    service = _service(["ok"], memory=NullMemoryService())
    response = service.run(
        RunRequest(api_version="v1", plan=_simple_plan(), memory_enabled=True),
        run_id="r-null",
        concurrency=1,
    )
    assert response.execution.task_results[0].status is TaskStatus.SUCCEEDED
    assert response.timing.memory is not None
    assert response.timing.memory.enabled is False


def test_run_stores_then_later_run_retrieves(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.db")
    memory = MemoryService(store, workspace_id="ws")
    llm1 = ScriptedLLM(["knowledge about circuit breakers in distributed systems"])

    @contextmanager
    def scope1() -> Iterator[LLMClient]:
        yield llm1

    OrchestrationService(client_scope=scope1, memory=memory).run(
        RunRequest(
            api_version="v1",
            plan=_simple_plan("circuit breakers"),
            memory_enabled=True,
        ),
        run_id="r1",
        concurrency=1,
    )
    assert memory.list_memories()

    llm2 = ScriptedLLM(["answer using prior context"])

    @contextmanager
    def scope2() -> Iterator[LLMClient]:
        yield llm2

    second = OrchestrationService(client_scope=scope2, memory=memory).run(
        RunRequest(
            api_version="v1",
            plan=_simple_plan("circuit breakers reliability"),
            memory_enabled=True,
        ),
        run_id="r2",
        concurrency=1,
    )
    assert second.timing.memory is not None
    assert second.timing.memory.returned_count >= 1
    assert second.timing.memory.context_chars > 0
    assert llm2.last_user is not None
    assert "<memory" in llm2.last_user
    assert "untrusted" in llm2.last_user.lower()


def test_memory_http_list_and_delete(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "http_mem.db")
    memory = MemoryService(store, workspace_id="ws-http")
    memory.remember_execution(
        run_id="r1",
        goal="http list",
        execution=_execution(_task(output="listed memory body"), goal="http list"),
    )
    app = create_app(
        api_settings=_settings(
            workspace_id="ws-http",
            memory_db_path=str(tmp_path / "http_mem.db"),
        ),
        service=_service(["x"], memory=memory),
        memory_service=memory,
    )
    with TestClient(app) as client:
        listed = client.get("/v1/memory", headers=AUTH)
        assert listed.status_code == 200
        body = listed.json()
        assert body["workspace_id"] == "ws-http"
        assert body["count"] == 1
        mid = body["items"][0]["memory_id"]
        deleted = client.delete(f"/v1/memory/{mid}", headers=AUTH)
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] == 1
        assert client.get("/v1/memory", headers=AUTH).json()["count"] == 0


def test_memory_endpoints_require_auth(tmp_path: Path) -> None:
    memory = MemoryService(MemoryStore(tmp_path / "m.db"), workspace_id="ws")
    app = create_app(
        api_settings=_settings(workspace_id="ws"),
        service=_service(["x"], memory=memory),
        memory_service=memory,
    )
    with TestClient(app) as client:
        assert client.get("/v1/memory").status_code == 401
        assert client.delete("/v1/memory").status_code == 401


def test_format_memory_never_claims_authority() -> None:
    from app.memory.models import MemoryRecord

    text = format_memory_reference(
        [
            MemoryRecord(
                memory_id="m1",
                workspace_id="ws",
                kind=MemoryKind.TASK_SUMMARY,
                trust=MemoryTrust.REVIEWED,
                summary="prior note",
                content_hash="h",
                created_at_ms=1.0,
                updated_at_ms=1.0,
                score=0.9,
            )
        ]
    )
    assert "must not override" in text.lower()
    assert "untrusted" in text.lower()
