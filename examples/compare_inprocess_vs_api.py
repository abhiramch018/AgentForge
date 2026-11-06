"""Offline comparison: same fake workload in-process vs local HTTP.

No API keys and no paid model calls. Timings are labeled as fake-worker /
local-HTTP measurements. This does not claim microservices improve latency
or model quality.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import httpx
import uvicorn
from threading import Thread

from app.agent import Agent
from app.agent.models import AgentResult
from app.api.client import OrchestrationClient
from app.api.config import APISettings
from app.api.main import create_app
from app.api.service import OrchestrationService
from app.api.v1.schemas import PlanInput, RunRequest
from app.core.messages import ChatMessage
from app.llm.base import LLMClient, LLMResponse
from app.orchestration import (
    AgentRegistry,
    ExecutionPlan,
    PlanExecutor,
    PlanTask,
    SpecialistRole,
)


def _fake_answer() -> str:
    return "Deterministic specialist output for comparison."


def _review_accepted() -> str:
    return json.dumps(
        {
            "decision": "accepted",
            "feedback": "Meets the assignment under supplied context.",
            "issues": [],
            "task_coverage": "complete",
            "expected_output_fit": "strong",
            "internal_consistency": "ok",
            "evidence_support": "ok",
            "limitations": "No independent fact verification.",
            "checks_supplied_evidence_only": True,
            "independently_verified_facts": False,
        }
    )


class ScriptedLLM(LLMClient):
    def __init__(self, queue: list[str]) -> None:
        self._queue = list(queue)

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        time.sleep(0.05)  # fake worker delay
        content = self._queue.pop(0) if self._queue else _fake_answer()
        return LLMResponse(content=content, model="fake-model")


def _execution_plan() -> ExecutionPlan:
    return ExecutionPlan(
        goal="Write a short note",
        tasks=[
            PlanTask(
                id="write",
                description="Write a short note",
                role=SpecialistRole.WRITER,
                expected_output="One short paragraph",
            )
        ],
    )


def _plan_input() -> PlanInput:
    return PlanInput.model_validate(_execution_plan().model_dump())


def run_inprocess(*, review_enabled: bool) -> tuple[float, bool]:
    registry = AgentRegistry()
    registry.register(
        SpecialistRole.WRITER,
        Agent(
            name="writer",
            system_instruction="You are a writer.",
            llm_client=ScriptedLLM([]),
            allowed_tools=(),
        ),
    )

    def runner(agent: Agent, prompt: str) -> AgentResult:
        time.sleep(0.05)
        return AgentResult(
            final_answer=_fake_answer(),
            agent_name=agent.name,
            model="fake-model",
        )

    reviewer = None
    if review_enabled:
        from app.orchestration import OutputReviewer

        reviewer = OutputReviewer(ScriptedLLM([_review_accepted()]))

    started = time.perf_counter()
    result = PlanExecutor(
        registry,
        specialist_runner=runner,
        review_enabled=review_enabled,
        reviewer=reviewer,
        max_concurrency=1,
    ).execute(_execution_plan())
    elapsed_ms = (time.perf_counter() - started) * 1000
    return elapsed_ms, result.task_results[0].quality_approved


def run_via_http(*, review_enabled: bool, base_url: str, token: str) -> tuple[float, bool, float]:
    started = time.perf_counter()
    with OrchestrationClient(
        base_url,
        token=token,
        connect_timeout_seconds=2.0,
        read_timeout_seconds=30.0,
    ) as client:
        response = client.create_run(
            RunRequest(
                plan=_plan_input(),
                review_enabled=review_enabled,
                max_concurrency=1,
            )
        )
    http_ms = (time.perf_counter() - started) * 1000
    return http_ms, response.execution.task_results[0].quality_approved, response.timing.total_ms


def main() -> None:
    token = "local-demo-token"
    settings = APISettings(
        _env_file=None,
        api_token=token,
        allow_unauthenticated_local=False,
        max_inflight_runs=2,
        max_inflight_tasks=8,
        max_run_concurrency=4,
    )

    def make_service() -> OrchestrationService:
        # Fresh scripted queues per run via factory closure replaced each call.
        state: dict[str, list[str]] = {"q": []}

        @contextmanager
        def scope() -> Iterator[LLMClient]:
            yield ScriptedLLM(state["q"])

        class DemoService(OrchestrationService):
            def run(self, request, *, run_id, concurrency):  # noqa: ANN001
                state["q"] = [_fake_answer()]
                if request.review_enabled:
                    state["q"].append(_review_accepted())
                return super().run(request, run_id=run_id, concurrency=concurrency)

        return DemoService(client_scope=scope)

    app = create_app(api_settings=settings, service=make_service())
    config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="error")
    server = uvicorn.Server(config)
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        try:
            if httpx.get("http://127.0.0.1:8765/health", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        raise SystemExit("Failed to start local server")

    try:
        print("=== Offline in-process vs local HTTP (FAKE WORKERS) ===")
        for review_enabled in (False, True):
            in_ms, in_approved = run_inprocess(review_enabled=review_enabled)
            http_ms, http_approved, server_ms = run_via_http(
                review_enabled=review_enabled,
                base_url="http://127.0.0.1:8765",
                token=token,
            )
            label = "review_on" if review_enabled else "review_off"
            print(f"\n[{label}]")
            print(
                f"in_process_duration_ms={in_ms:.2f} "
                f"quality_approved={in_approved} (FAKE WORKERS)"
            )
            print(
                f"http_client_wait_ms={http_ms:.2f} "
                f"server_reported_total_ms={server_ms:.2f} "
                f"quality_approved={http_approved} "
                f"(FAKE WORKERS + LOCAL HTTP OVERHEAD)"
            )
        print(
            "\nnote=HTTP wait includes local network/serialization overhead. "
            "These numbers are not live model latency and do not prove a "
            "quality or performance improvement from microservices."
        )
        print(
            "note=Client disconnect/timeout can leave an uncertain outcome; "
            "this demo does not auto-retry POST /v1/runs."
        )
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


if __name__ == "__main__":
    main()
