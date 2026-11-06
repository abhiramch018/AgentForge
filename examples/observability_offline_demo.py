"""Offline M12 demo: timeline, latency, tokens, and fixture cost estimate.

Uses fake LLMs with explicit TokenUsage — no paid API calls.

Windows::

    python -m examples.observability_offline_demo
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
from app.api.jobs.events import JobEventStore
from app.api.jobs.queue import JobQueue
from app.api.jobs.runtime import JobRuntime, set_runtime
from app.api.jobs.store import JobStore
from app.api.main import create_app
from app.api.service import OrchestrationService
from app.core.messages import ChatMessage
from app.llm.base import LLMClient, LLMResponse, TokenUsage
from app.memory.service import NullMemoryService
from app.observability.tracing import reset_tracing_for_tests


class ScriptedLLM(LLMClient):
    def __init__(self, answers: Sequence[str], *, usage: TokenUsage) -> None:
        self._answers = list(answers)
        self._usage = usage

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        content = self._answers.pop(0) if self._answers else "done"
        return LLMResponse(content=content, model="fake-model", usage=self._usage)


def main() -> None:
    reset_tracing_for_tests()
    usage = TokenUsage(prompt_tokens=1200, completion_tokens=400, total_tokens=1600)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        llm = ScriptedLLM(["M12 offline specialist answer"], usage=usage)

        @contextmanager
        def scope() -> Iterator[LLMClient]:
            yield llm

        events = JobEventStore(root / "jobs.db", max_events_per_job=100)
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
            worker_id="obs-demo",
        )
        set_runtime(runtime)
        settings = APISettings(
            _env_file=None,
            api_token="demo-token",
            allow_unauthenticated_local=False,
            jobs_immediate=True,
            memory_enabled_default=False,
        )
        app = create_app(
            api_settings=settings,
            service=service,
            job_runtime=runtime,
            memory_service=NullMemoryService(),
            event_store=events,
        )
        headers = {"Authorization": "Bearer demo-token"}
        plan = {
            "goal": "Demonstrate AgentForge M12 observability",
            "tasks": [
                {
                    "id": "t1",
                    "description": "Write one sentence about explainable runs",
                    "role": "general",
                    "depends_on": [],
                    "expected_output": "One sentence",
                }
            ],
        }
        with TestClient(app) as client:
            print("1) POST /v1/jobs (fake LLM + fixture pricing)")
            submit = client.post(
                "/v1/jobs",
                headers=headers,
                json={
                    "api_version": "v1",
                    "plan": plan,
                    "memory_enabled": False,
                },
            )
            print(f"   status={submit.status_code}")
            job_id = submit.json()["job_id"]

            print("2) GET /v1/jobs/{id}/replay  (timeline playback — not a rerun)")
            replay = client.get(f"/v1/jobs/{job_id}/replay", headers=headers)
            replay_body = replay.json()
            print(f"   mode={replay_body['mode']} events={replay_body['event_count']}")
            latency = replay_body["latency"]
            print(
                "   latency_ms:",
                json.dumps(
                    {
                        "queue_wait": latency.get("queue_wait_ms"),
                        "planning": latency.get("planning_ms"),
                        "execution": latency.get("execution_ms"),
                        "approval_wait": latency.get("approval_wait_ms"),
                        "total": latency.get("total_ms"),
                    },
                    indent=2,
                ),
            )
            print("   sample timeline:")
            for event in replay_body["events"][:8]:
                print(
                    f"     #{event['event_id']} {event['event_type']} "
                    f"+{event.get('delta_ms')}ms"
                )

            print("3) GET /v1/jobs/{id}/observability")
            obs = client.get(
                f"/v1/jobs/{job_id}/observability", headers=headers
            ).json()
            usage_block = obs["usage"]
            cost = obs["cost"]
            print(
                "   usage:",
                json.dumps(
                    {
                        "coverage": usage_block["coverage"],
                        "prompt_tokens": usage_block["prompt_tokens"],
                        "completion_tokens": usage_block["completion_tokens"],
                        "total_tokens": usage_block["total_tokens"],
                    },
                    indent=2,
                ),
            )
            print(
                "   cost_estimate (fixture pricing — not an invoice):",
                json.dumps(
                    {
                        "kind": cost["kind"],
                        "as_of": cost["as_of"],
                        "provenance": cost["provenance"],
                        "coverage": cost["coverage"],
                        "total_usd": cost["total_usd"],
                        "currency": cost["currency"],
                        "unit": cost["unit"],
                    },
                    indent=2,
                ),
            )
            print("   cost notes:")
            for note in cost["notes"][:6]:
                print(f"     - {note}")
            if obs.get("trace_id"):
                print(f"   trace_id={obs['trace_id']}")

            print("4) Distinctions")
            print("   - Replay above is recorded timeline playback only.")
            print("   - A true rerun requires a new POST /v1/jobs + current approvals.")
            print("   - Missing provider usage would be labeled partial/unavailable, not zero.")


if __name__ == "__main__":
    main()
