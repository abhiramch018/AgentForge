"""Background worker process for AgentForge jobs.

Windows startup (from project root, venv active)::

    python -m app.api.jobs.worker

Offline demonstration (fake LLMs, no paid calls)::

    python -m app.api.jobs.worker --offline

This process consumes Huey queue messages and executes claimed jobs using the
same orchestration engine as ``POST /v1/runs``. Capacity limits below are
**per worker process**, not deployment-wide.

Queue messages contain only ``job_id``. Credentials and results stay out of
the queue. Authoritative state lives in the SQLite job store.

Shutdown notes
--------------
SIGTERM/SIGINT stops the Huey consumer from taking *new* work. An in-flight
LLM HTTP call is **not** forcibly cancelled — the provider request may finish
or time out on its own. After restart:

* ``running`` jobs with expired leases are re-enqueued (lease recovery).
* ``awaiting_approval`` jobs stay paused (no worker lease) until a human
  decision; they are not re-enqueued by recovery.
* ``queued`` jobs awaiting delivery are re-enqueued on startup.

Graceful shutdown therefore releases capacity eventually, but does not claim
to abort an active model call mid-flight.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from huey.consumer import Consumer

from app.api.config import load_api_settings
from app.api.jobs.runtime import JobRuntime
from app.api.readiness import all_ready, check_worker_readiness

logger = logging.getLogger(__name__)

_consumer_started = threading.Event()
_runtime_ref: JobRuntime | None = None
_settings_paths: dict[str, str] = {}


def build_runtime_from_env(*, offline: bool = False) -> JobRuntime:
    if offline or os.environ.get("ORCHESTRATION_OFFLINE_DEMO", "").lower() in {
        "1",
        "true",
        "yes",
    }:
        from app.api.offline import build_offline_runtime

        logger.warning(
            "OFFLINE_DEMO worker — DemoLLM only; no paid provider calls"
        )
        return build_offline_runtime()

    from app.api.concurrency import CapacityGate
    from app.api.jobs.events import JobEventStore
    from app.api.jobs.queue import configure_default_queue
    from app.api.jobs.runtime import set_runtime
    from app.api.jobs.store import JobStore
    from app.api.service import OrchestrationService
    from app.approval.service import ApprovalService
    from app.approval.store import ApprovalStore
    from app.memory.service import MemoryService, NullMemoryService
    from app.memory.store import MemoryStore

    api = load_api_settings()
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
        )
    except Exception:
        logger.exception("worker_memory_init_failed")
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


class _WorkerHealthHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        logger.debug("worker_health %s", format % args)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/health":
            body = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.split("?", 1)[0] == "/ready":
            runtime = _runtime_ref
            checks = check_worker_readiness(
                jobs_db_path=_settings_paths.get("jobs_db_path", "data/jobs.db"),
                queue_db_path=_settings_paths.get(
                    "queue_db_path", "data/huey_queue.db"
                ),
                job_store=None if runtime is None else runtime.store,
                consumer_started=_consumer_started.is_set(),
            )
            ready = all_ready(checks)
            payload = {
                "status": "ready" if ready else "not_ready",
                "checks": checks,
            }
            body = json.dumps(payload).encode()
            self.send_response(200 if ready else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


def _start_health_server(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), _WorkerHealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("worker_health_listen port=%s", port)
    return server


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="AgentForge job worker")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use DemoLLM (no paid API calls). Labeled offline demo mode.",
    )
    parser.add_argument(
        "--health-port",
        type=int,
        default=int(os.environ.get("WORKER_HEALTH_PORT", "8090")),
        help="HTTP port for /health and /ready (default 8090).",
    )
    args = parser.parse_args(argv)

    global _runtime_ref
    api = load_api_settings()
    _settings_paths.clear()
    _settings_paths.update(
        {
            "jobs_db_path": api.jobs_db_path,
            "queue_db_path": api.queue_db_path,
        }
    )

    runtime = build_runtime_from_env(offline=args.offline)
    _runtime_ref = runtime
    recovered = runtime.recover()
    logger.info(
        "worker_start worker_id=%s recovered_jobs=%s offline=%s",
        runtime.worker_id,
        len(recovered),
        args.offline
        or os.environ.get("ORCHESTRATION_OFFLINE_DEMO", "").lower()
        in {"1", "true", "yes"},
    )

    health_server = _start_health_server(args.health_port)

    stop = threading.Event()

    def recovery_loop() -> None:
        while not stop.wait(2.0):
            try:
                runtime.recover()
            except Exception:
                logger.exception("recovery_loop_failed")

    thread = threading.Thread(target=recovery_loop, daemon=True)
    thread.start()

    consumer = Consumer(
        runtime.queue.huey,
        workers=1,
        worker_type="thread",
        initial_delay=0.1,
        backoff=1.15,
        max_delay=2.0,
        check_worker_health=True,
    )
    _consumer_started.set()

    def _handle_signal(signum: int, _frame: object) -> None:
        logger.info(
            "worker_signal signum=%s — stopping consumer; "
            "in-flight LLM calls are not forcibly cancelled",
            signum,
        )
        stop.set()
        try:
            consumer.stop()
        except Exception:
            logger.exception("consumer_stop_failed")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        consumer.run()
    finally:
        stop.set()
        _consumer_started.clear()
        health_server.shutdown()
        logger.info("worker_shutdown_complete")


if __name__ == "__main__":
    main()
