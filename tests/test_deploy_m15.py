"""M15 packaging helpers: readiness probes, offline factories, SQLite backup."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.config import APISettings
from app.api.main import create_app
from app.api.readiness import all_ready, check_api_readiness
from app.api.service import OrchestrationService
from app.llm.demo import DemoLLM
from deploy.backup_sqlite import backup_one
from deploy.restore_sqlite import DB_FILES


def _settings(tmp: Path) -> APISettings:
    return APISettings(
        _env_file=None,
        api_token="test-token",
        allow_unauthenticated_local=False,
        jobs_immediate=True,
        jobs_db_path=str(tmp / "jobs.db"),
        queue_db_path=str(tmp / "huey.db"),
        memory_db_path=str(tmp / "memory.db"),
        approvals_db_path=str(tmp / "approvals.db"),
        artifact_output_root=str(tmp / "artifacts"),
    )


def test_ready_endpoint_ok(tmp_path: Path) -> None:
    app = create_app(api_settings=_settings(tmp_path), service=OrchestrationService())
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        ready = client.get("/ready")
        assert ready.status_code == 200
        body = ready.json()
        assert body["status"] == "ready"
        assert body["checks"]["job_store"] is True
        assert all(body["checks"].values())


def test_ready_fails_when_artifact_root_not_writable(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    # Point artifact root at a file path so mkdir/write fails.
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    settings.artifact_output_root = str(blocker)
    app = create_app(api_settings=settings, service=OrchestrationService())
    with TestClient(app) as client:
        ready = client.get("/ready")
        assert ready.status_code == 503
        assert ready.json()["status"] == "not_ready"
        assert ready.json()["checks"]["artifact_root"] is False


def test_check_api_readiness_helper(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs.db"
    checks = check_api_readiness(
        jobs_db_path=str(jobs),
        queue_db_path=str(tmp_path / "q.db"),
        memory_db_path=str(tmp_path / "m.db"),
        approvals_db_path=str(tmp_path / "a.db"),
        events_db_path=str(jobs),
        artifact_root=str(tmp_path / "art"),
        job_store=None,
    )
    assert all_ready(checks)


def test_demo_llm_deterministic() -> None:
    llm = DemoLLM()
    from app.core.messages import ChatMessage, MessageRole

    r = llm.generate(
        [ChatMessage(role=MessageRole.USER, content="planning supervisor lean execution plan")]
    )
    assert "tasks" in r.content
    r2 = llm.generate([ChatMessage(role=MessageRole.USER, content="do the work")])
    assert "AgentForge" in r2.content


def test_offline_app_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATION_API_TOKEN", "offline-token")
    monkeypatch.setenv("ORCHESTRATION_JOBS_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("ORCHESTRATION_QUEUE_DB_PATH", str(tmp_path / "huey.db"))
    monkeypatch.setenv("ORCHESTRATION_MEMORY_DB_PATH", str(tmp_path / "memory.db"))
    monkeypatch.setenv("ORCHESTRATION_APPROVALS_DB_PATH", str(tmp_path / "approvals.db"))
    monkeypatch.setenv("ORCHESTRATION_ARTIFACT_OUTPUT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ORCHESTRATION_ALLOW_UNAUTHENTICATED_LOCAL", "false")
    monkeypatch.delenv("ORCHESTRATION_OFFLINE_DEMO", raising=False)

    from app.api.offline import create_offline_app

    app = create_offline_app()
    assert getattr(app.state, "offline_demo", False) is True
    with TestClient(app) as client:
        ready = client.get("/ready")
        assert ready.status_code == 200
        assert ready.json()["offline_demo"] is True
        meta = client.get(
            "/v1/meta", headers={"Authorization": "Bearer offline-token"}
        )
        assert meta.status_code == 200
        submitted = client.post(
            "/v1/jobs",
            headers={"Authorization": "Bearer offline-token"},
            json={"goal": "Offline demo job for AgentForge", "review_enabled": False},
        )
        assert submitted.status_code == 202
        job_id = submitted.json()["job_id"]
        # Immediate is forced false in offline factory — job stays queued until worker.
        status = client.get(
            f"/v1/jobs/{job_id}",
            headers={"Authorization": "Bearer offline-token"},
        )
        assert status.status_code == 200
        assert status.json()["status"] in {"queued", "running", "succeeded"}


def test_sqlite_backup_and_restore_roundtrip(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    src = data / "jobs.db"
    with sqlite3.connect(str(src)) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t(v) VALUES ('keep-me')")
        conn.commit()

    backup_dir = tmp_path / "backup"
    backup_one(src, backup_dir / "jobs.db")
    assert (backup_dir / "jobs.db").exists()

    restored = tmp_path / "restored" / "jobs.db"
    import shutil

    restored.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_dir / "jobs.db", restored)
    with sqlite3.connect(str(restored)) as conn:
        row = conn.execute("SELECT v FROM t").fetchone()
    assert row[0] == "keep-me"
    assert "jobs.db" in DB_FILES


def test_compose_files_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "docker-compose.yml").is_file()
    assert (root / "docker-compose.offline.yml").is_file()
    assert (root / "Dockerfile.api").is_file()
    assert (root / "Dockerfile.frontend").is_file()
    assert (root / ".dockerignore").is_file()
    assert (root / ".env.example").is_file()
    assert (root / "DEPLOY.md").is_file()
    text = (root / "docker-compose.offline.yml").read_text(encoding="utf-8")
    assert "OFFLINE DEMO" in text
    assert "DemoLLM" in text or "offline" in text.lower()
    nginx = (root / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    assert "proxy_buffering off" in nginx
