"""CLI demo: submit a job and stream durable lifecycle SSE (fake LLM).

Uses Authorization header (not a token in the URL). Events are lifecycle
markers — not fabricated model-token chunks.

Windows::

    python -m examples.stream_job_events_demo
"""

from __future__ import annotations

import tempfile
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import httpx
import uvicorn

from app.api.concurrency import CapacityGate
from app.api.config import APISettings
from app.api.jobs.events import JobEventStore
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
    def __init__(self, answers: Sequence[str]) -> None:
        self._answers = list(answers)

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        time.sleep(0.05)
        content = self._answers.pop(0) if self._answers else "done"
        return LLMResponse(content=content, model="fake-model")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        events = JobEventStore(root / "jobs.db")
        llm = ScriptedLLM(["Streaming demo specialist output."])

        @contextmanager
        def scope() -> Iterator[LLMClient]:
            yield llm

        service = OrchestrationService(
            client_scope=scope,
            memory=NullMemoryService(),
            events=events,
        )
        runtime = JobRuntime(
            store=JobStore(root / "jobs.db"),
            queue=JobQueue(root / "huey.db", immediate=True),
            service=service,
            capacity=CapacityGate(max_runs=2, max_tasks=8),
            max_run_concurrency=4,
            events=events,
            worker_id="stream-demo",
        )
        set_runtime(runtime)
        token = "demo-token"
        app = create_app(
            api_settings=APISettings(
                _env_file=None,
                api_token=token,
                jobs_immediate=True,
                events_poll_interval_ms=50.0,
                events_heartbeat_seconds=20.0,
            ),
            service=service,
            job_runtime=runtime,
            event_store=events,
            memory_service=NullMemoryService(),
        )

        import socket

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        )
        threading.Thread(target=server.run, daemon=True).start()
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                if httpx.get(f"{base}/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)

        job = runtime.submit(
            RunRequest(
                api_version="v1",
                plan=PlanInput(
                    goal="Stream demo",
                    tasks=[
                        {
                            "id": "t1",
                            "description": "Write a note",
                            "role": "general",
                            "depends_on": [],
                            "expected_output": "Note",
                        }
                    ],
                ),
                memory_enabled=False,
            )
        )
        print(f"job_id={job.job_id} status={job.status.value}")
        print("streaming lifecycle events (Authorization: Bearer …):")
        with httpx.Client(timeout=15.0) as client:
            with client.stream(
                "GET",
                f"{base}/v1/jobs/{job.job_id}/events",
                headers={"Authorization": f"Bearer {token}"},
            ) as response:
                for line in response.iter_lines():
                    if line.startswith("event:") or line.startswith("data:"):
                        print(f"  {line}")
                    if line.startswith("event:") and "job.succeeded" in line:
                        break
        print(
            "\nnote=stream_kind=lifecycle only; not live token generation. "
            "Native EventSource cannot set Bearer — use fetch streaming."
        )
        server.should_exit = True


if __name__ == "__main__":
    main()
