"""Offline M8 demo: submit → poll → result with fake LLMs (no paid calls).

Runs an in-process FastAPI app with immediate Huey delivery so a separate
worker process is not required for this demonstration.

Windows::

    python -m examples.jobs_offline_demo
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.concurrency import CapacityGate
from app.api.config import APISettings
from app.api.jobs.queue import JobQueue
from app.api.jobs.runtime import JobRuntime, set_runtime
from app.api.jobs.store import JobStore
from app.api.main import create_app
from app.api.service import OrchestrationService
from app.core.messages import ChatMessage
from app.llm.base import LLMClient, LLMResponse


class ScriptedLLM(LLMClient):
    def __init__(self, answers: Sequence[str]) -> None:
        self._answers = list(answers)

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        content = self._answers.pop(0) if self._answers else "done"
        return LLMResponse(content=content, model="fake-model")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        llm = ScriptedLLM(["portfolio-quality specialist answer"])

        @contextmanager
        def scope() -> Iterator[LLMClient]:
            yield llm

        service = OrchestrationService(client_scope=scope)
        store = JobStore(root / "jobs.db")
        queue = JobQueue(root / "huey.db", immediate=True)
        runtime = JobRuntime(
            store=store,
            queue=queue,
            service=service,
            capacity=CapacityGate(max_runs=2, max_tasks=8),
            max_run_concurrency=4,
            max_job_attempts=2,
            lease_ms=60_000,
            worker_id="offline-demo",
        )
        set_runtime(runtime)
        settings = APISettings(
            _env_file=None,
            api_token="demo-token",
            allow_unauthenticated_local=False,
            jobs_immediate=True,
        )
        app = create_app(
            api_settings=settings,
            service=service,
            job_runtime=runtime,
        )
        headers = {"Authorization": "Bearer demo-token"}
        plan = {
            "goal": "Summarize AgentForge M8",
            "tasks": [
                {
                    "id": "t1",
                    "description": "Write a one-sentence summary",
                    "role": "general",
                    "depends_on": [],
                    "expected_output": "One sentence",
                }
            ],
        }
        with TestClient(app) as client:
            print("1) POST /v1/jobs")
            submit = client.post(
                "/v1/jobs",
                headers=headers,
                json={"api_version": "v1", "plan": plan},
            )
            print(f"   status={submit.status_code} body={submit.json()}")
            job_id = submit.json()["job_id"]

            print("2) GET /v1/jobs/{id}")
            status = client.get(f"/v1/jobs/{job_id}", headers=headers)
            print(f"   status={status.status_code}")
            print(f"   {json.dumps(status.json(), indent=2)}")

            print("3) GET /v1/jobs/{id}/result")
            result = client.get(f"/v1/jobs/{job_id}/result", headers=headers)
            body = result.json()
            print(f"   status={result.status_code}")
            timing = body.get("timing", {})
            print(
                "   timings: "
                f"queue_wait_ms={timing.get('queue_wait_ms')} "
                f"execute_ms={timing.get('execute_ms')} "
                f"total_ms={timing.get('total_ms')}"
            )
            task = body["result"]["execution"]["task_results"][0]
            print(
                f"   task status={task['status']} "
                f"quality_approved={task['quality_approved']} "
                f"output={task['output']!r}"
            )
            print(
                "\nNote: immediate Huey runs in-process for this offline demo. "
                "Production local path uses a separate worker:\n"
                "  uvicorn app.api.main:create_app --factory --port 8000\n"
                "  python -m app.api.jobs.worker"
            )


if __name__ == "__main__":
    main()
