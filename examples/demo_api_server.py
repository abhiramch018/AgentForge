"""Local demo API for the M14 dashboard — fake LLMs, no paid calls.

Labeled demonstration server with immediate job delivery.

Windows::

    python -m examples.demo_api_server

Token: ``demo-token``  URL: http://127.0.0.1:8000
Point the Vite proxy / frontend at this process.

For the full Docker offline stack (API + separate worker + persistence), see
``DEPLOY.md`` and ``docker compose -f docker-compose.yml -f docker-compose.offline.yml``.
"""

from __future__ import annotations

import argparse
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import uvicorn

from app.api.concurrency import CapacityGate
from app.api.config import APISettings
from app.api.jobs.events import JobEventStore
from app.api.jobs.queue import JobQueue
from app.api.jobs.runtime import JobRuntime, set_runtime
from app.api.jobs.store import JobStore
from app.api.main import create_app
from app.api.service import OrchestrationService
from app.approval.service import ApprovalService
from app.approval.store import ApprovalStore
from app.llm.base import LLMClient
from app.llm.demo import DemoLLM
from app.memory.embeddings import HashingEmbedder
from app.memory.service import MemoryService
from app.memory.store import MemoryStore


def build_demo_app(root: Path, *, token: str = "demo-token"):
    llm = DemoLLM()

    @contextmanager
    def scope() -> Iterator[LLMClient]:
        yield llm

    events = JobEventStore(root / "jobs.db", max_events_per_job=500)
    memory = MemoryService(
        MemoryStore(root / "memory.db"),
        workspace_id="demo",
        embedder=HashingEmbedder(),
    )
    approvals = ApprovalService(
        ApprovalStore(root / "approvals.db"),
        workspace_id="demo",
        artifact_root=str(root / "artifacts"),
        ttl_ms=3_600_000,
    )
    service = OrchestrationService(
        client_scope=scope,
        memory=memory,
        approval=approvals,
        events=events,
    )
    runtime = JobRuntime(
        store=JobStore(root / "jobs.db"),
        queue=JobQueue(root / "huey.db", immediate=True),
        service=service,
        capacity=CapacityGate(max_runs=4, max_tasks=16),
        max_run_concurrency=4,
        events=events,
        approval=approvals,
        worker_id="demo-api",
    )
    set_runtime(runtime)
    settings = APISettings(
        _env_file=None,
        api_token=token,
        allow_unauthenticated_local=False,
        jobs_immediate=True,
        workspace_id="demo",
        memory_enabled_default=True,
    )
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


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentForge demo API (fake LLMs)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--token", default="demo-token")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Persist demo SQLite files (default: temp dir for the process lifetime)",
    )
    args = parser.parse_args()
    if args.data_dir is None:
        tmp = tempfile.TemporaryDirectory(prefix="agentforge-demo-")
        root = Path(tmp.name)
        print(f"Demo data (ephemeral): {root}")
    else:
        tmp = None
        root = args.data_dir
        root.mkdir(parents=True, exist_ok=True)
        print(f"Demo data: {root}")
    app = build_demo_app(root, token=args.token)
    print("DEMONSTRATION MODE — fake LLMs; no paid API calls.")
    print(f"Bearer token: {args.token}")
    print(f"Listening on http://{args.host}:{args.port}")
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        if tmp is not None:
            tmp.cleanup()


if __name__ == "__main__":
    main()
