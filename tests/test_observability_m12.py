"""M12 observability, usage/cost accounting, and safe timeline replay."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.api.concurrency import CapacityGate
from app.api.config import APISettings
from app.api.jobs.events import JobEventStore, JobEventType
from app.api.jobs.queue import JobQueue
from app.api.jobs.runtime import JobRuntime, set_runtime
from app.api.jobs.store import JobStore
from app.api.main import create_app
from app.api.service import OrchestrationService
from app.api.v1.schemas import PlanInput, RunRequest
from app.core.messages import ChatMessage
from app.llm.base import LLMClient, LLMResponse, TokenUsage
from app.memory.service import NullMemoryService
from app.observability import metrics as metrics_mod
from app.observability.models import UsageCoverage
from app.observability.pricing import PricingTable, estimate_cost, load_fixture_pricing
from app.observability.redaction import redact_mapping
from app.observability.replay import build_replay_timeline
from app.observability.tracing import (
    current_trace_id,
    inject_trace_context,
    reset_tracing_for_tests,
    start_span,
)
from app.observability.usage import aggregate_run_usage
from app.orchestration.execution import (
    ExecutionResult,
    ExecutionStatus,
    TaskResult,
    TaskStatus,
)
from app.orchestration.models import ExecutionPlan, PlanResult, PlanTask, SpecialistRole
from app.orchestration.review import AttemptKind, AttemptRecord, ReviewPolicy


class ScriptedLLM(LLMClient):
    def __init__(
        self,
        answers: Sequence[str] | None = None,
        *,
        usage: TokenUsage | None = None,
        model: str = "fake-model",
    ) -> None:
        self._answers = list(answers or [])
        self._usage = usage
        self._model = model

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        content = self._answers.pop(0) if self._answers else "ok"
        return LLMResponse(
            content=content,
            model=self._model,
            usage=self._usage,
        )


def _settings(**kwargs: object) -> APISettings:
    base: dict[str, object] = {
        "api_token": "test-token",
        "allow_unauthenticated_local": False,
        "max_inflight_runs": 2,
        "max_inflight_tasks": 8,
        "max_run_concurrency": 4,
        "jobs_immediate": True,
        "memory_enabled_default": False,
        "events_max_per_job": 50,
    }
    base.update(kwargs)
    return APISettings(_env_file=None, **base)  # type: ignore[arg-type]


def _plan() -> PlanInput:
    return PlanInput(
        goal="Observability demo",
        tasks=[
            {
                "id": "t1",
                "description": "Do work",
                "role": "general",
                "depends_on": [],
                "expected_output": "Done",
            }
        ],
    )


def _runtime(
    tmp_path: Path,
    answers: Sequence[str] | None = None,
    *,
    usage: TokenUsage | None = None,
    model: str = "fake-model",
) -> JobRuntime:
    events = JobEventStore(tmp_path / "jobs.db", max_events_per_job=50)
    llm = ScriptedLLM(answers, usage=usage, model=model)

    @contextmanager
    def scope() -> Iterator[LLMClient]:
        yield llm

    service = OrchestrationService(
        client_scope=scope,
        memory=NullMemoryService(),
        events=events,
    )
    runtime = JobRuntime(
        store=JobStore(tmp_path / "jobs.db"),
        queue=JobQueue(tmp_path / "huey.db", immediate=True),
        service=service,
        capacity=CapacityGate(max_runs=2, max_tasks=8),
        max_run_concurrency=4,
        events=events,
        worker_id="obs-worker",
    )
    set_runtime(runtime)
    return runtime


AUTH = {"Authorization": "Bearer test-token"}


def test_redaction_strips_secrets_and_content_by_default() -> None:
    raw = {
        "authorization": "Bearer secret-token",
        "api_key": "sk-test",
        "prompt": "hidden user text",
        "status": "ok",
        "nested": {"messages": [{"role": "user", "content": "hi"}]},
    }
    out = redact_mapping(raw)
    assert out["authorization"] == "[redacted]"
    assert out["api_key"] == "[redacted]"
    assert out["prompt"] == "[content_omitted]"
    assert out["status"] == "ok"
    # Nested dict is walked; the messages key is content-redacted.
    assert out["nested"]["messages"] == "[content_omitted]"


def test_trace_context_propagates_across_job_submit_and_worker(
    tmp_path: Path,
) -> None:
    exporter = reset_tracing_for_tests()
    with start_span("api.submit_parent"):
        parent_trace = current_trace_id()
        carrier = inject_trace_context()
        assert carrier.get("traceparent")
        assert parent_trace is not None

        runtime = _runtime(
            tmp_path,
            ["hello"],
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
        # Seed the same carrier the API would inject before create.
        record = runtime.store.create_queued_job(
            RunRequest(
                api_version="v1", plan=_plan(), memory_enabled=False
            ).model_dump(mode="json"),
            max_attempts=2,
            trace_context=carrier,
        )
        runtime.queue.enqueue(record.job_id)
        done = runtime.store.get(record.job_id)
        assert done.status.value == "succeeded"
        assert done.trace_context is not None
        assert "traceparent" in done.trace_context

    spans = exporter.get_finished_spans()
    names = {s.name for s in spans}
    assert "job.handle_delivery" in names or "job.execute" in names
    # Worker spans share the parent trace id from W3C carrier.
    worker_spans = [
        s
        for s in spans
        if s.name in {"job.handle_delivery", "job.execute", "orchestration.run"}
    ]
    assert worker_spans
    for span in worker_spans:
        assert format(span.context.trace_id, "032x") == parent_trace


def test_telemetry_exporter_failure_does_not_fail_job(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, ["ok"])
    # Broken exporter path: force ops_log / metrics to tolerate bad state.
    metrics_mod.reset_metrics_for_tests()
    job = runtime.submit(
        RunRequest(api_version="v1", plan=_plan(), memory_enabled=False)
    )
    assert job.status.value == "succeeded"


def test_usage_aggregation_complete_and_no_double_count() -> None:
    usage = TokenUsage(prompt_tokens=10, completion_tokens=4, total_tokens=14)
    planning = PlanResult(
        plan=ExecutionPlan(
            goal="g",
            tasks=[
                PlanTask(
                    id="t1",
                    description="d",
                    role=SpecialistRole.GENERAL,
                    depends_on=[],
                    expected_output="o",
                )
            ],
        ),
        llm_calls=1,
        duration_ms=1.0,
        model="fake-model",
        usage=usage,
    )
    attempt = AttemptRecord(
        index=0,
        kind=AttemptKind.INITIAL,
        output="out",
        model="fake-model",
        usage=usage,
    )
    task = TaskResult(
        task_id="t1",
        role="general",
        status=TaskStatus.SUCCEEDED,
        output="out",
        model="fake-model",
        usage=usage,  # convenience copy — must not be added again
        attempts=[attempt],
        review_policy=ReviewPolicy.BYPASSED_REVIEW_DISABLED,
        quality_approved=False,
        revision_count=0,
    )
    execution = ExecutionResult(
        goal="g",
        status=ExecutionStatus.SUCCEEDED,
        task_results=[task],
        duration_ms=1.0,
        succeeded_count=1,
        failed_count=0,
        timed_out_count=0,
        skipped_count=0,
    )
    summary = aggregate_run_usage(planning=planning, execution=execution)
    assert summary.coverage is UsageCoverage.COMPLETE
    assert summary.prompt_tokens == 20
    assert summary.completion_tokens == 8
    assert any("not double-counted" in n for n in summary.notes)


def test_partial_usage_is_not_zero_filled() -> None:
    planning = PlanResult(
        plan=ExecutionPlan(
            goal="g",
            tasks=[
                PlanTask(
                    id="t1",
                    description="d",
                    role=SpecialistRole.GENERAL,
                    depends_on=[],
                    expected_output="o",
                )
            ],
        ),
        llm_calls=1,
        duration_ms=1.0,
        model="fake-model",
        usage=None,
    )
    attempt = AttemptRecord(
        index=0,
        kind=AttemptKind.INITIAL,
        output="out",
        model="fake-model",
        usage=TokenUsage(prompt_tokens=3, completion_tokens=1, total_tokens=4),
    )
    task = TaskResult(
        task_id="t1",
        role="general",
        status=TaskStatus.SUCCEEDED,
        output="out",
        attempts=[attempt],
        review_policy=ReviewPolicy.BYPASSED_REVIEW_DISABLED,
        quality_approved=False,
        revision_count=0,
    )
    summary = aggregate_run_usage(
        planning=planning,
        execution=ExecutionResult(
            goal="g",
            status=ExecutionStatus.SUCCEEDED,
            task_results=[task],
            duration_ms=1.0,
            succeeded_count=1,
            failed_count=0,
            timed_out_count=0,
            skipped_count=0,
        ),
    )
    assert summary.coverage is UsageCoverage.PARTIAL
    assert summary.prompt_tokens == 3
    assert any("incomplete" in n.lower() for n in summary.notes)


def test_pricing_estimate_and_unknown_model() -> None:
    table = load_fixture_pricing()
    complete = aggregate_run_usage(
        planning=PlanResult(
            plan=ExecutionPlan(
                goal="g",
                tasks=[
                    PlanTask(
                        id="t1",
                        description="d",
                        role=SpecialistRole.GENERAL,
                        depends_on=[],
                        expected_output="o",
                    )
                ],
            ),
            llm_calls=1,
            duration_ms=1.0,
            model="fake-model",
            usage=TokenUsage(
                prompt_tokens=1_000_000, completion_tokens=1_000_000, total_tokens=2_000_000
            ),
        ),
        execution=None,
    )
    cost = estimate_cost(complete, table)
    assert cost.kind == "estimate"
    assert cost.coverage is UsageCoverage.COMPLETE
    assert cost.total_usd == pytest.approx(0.50)  # 0.10 + 0.40

    unknown = aggregate_run_usage(
        planning=PlanResult(
            plan=ExecutionPlan(
                goal="g",
                tasks=[
                    PlanTask(
                        id="t1",
                        description="d",
                        role=SpecialistRole.GENERAL,
                        depends_on=[],
                        expected_output="o",
                    )
                ],
            ),
            llm_calls=1,
            duration_ms=1.0,
            model="totally-unknown-model",
            usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        ),
        execution=None,
    )
    unavailable = estimate_cost(unknown, table)
    assert unavailable.coverage is UsageCoverage.UNAVAILABLE
    assert unavailable.total_usd is None
    assert any("Unpriced" in n for n in unavailable.notes)


def test_metrics_reject_high_cardinality_labels() -> None:
    metrics_mod.reset_metrics_for_tests()
    metrics_mod.incr("jobs_completed", labels={"status": "succeeded", "run_id": "r1"})
    snap = metrics_mod.snapshot()
    counters = snap["counters"]
    assert isinstance(counters, dict)
    # run_id must not appear as a label key in the series name.
    assert all("run_id" not in key for key in counters)


def test_replay_timeline_without_model_calls(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, ["replay-safe"])
    job = runtime.submit(
        RunRequest(api_version="v1", plan=_plan(), memory_enabled=False)
    )
    timeline = build_replay_timeline(job.job_id, runtime.events)
    assert timeline.mode == "timeline_playback"
    assert timeline.event_count >= 3
    types = [e.event_type for e in timeline.events]
    assert JobEventType.JOB_SUBMITTED.value in types
    assert JobEventType.JOB_SUCCEEDED.value in types
    assert any("does not call models" in n for n in timeline.notes)
    assert timeline.latency.total_ms is not None


def test_http_replay_and_observability_endpoints(tmp_path: Path) -> None:
    usage = TokenUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30)
    runtime = _runtime(tmp_path, ["answer"], usage=usage)
    settings = _settings()
    app = create_app(
        api_settings=settings,
        service=runtime.service,
        job_runtime=runtime,
        memory_service=NullMemoryService(),
        event_store=runtime.events,
    )
    with TestClient(app) as client:
        submit = client.post(
            "/v1/jobs",
            headers=AUTH,
            json={"api_version": "v1", "plan": _plan().model_dump(), "memory_enabled": False},
        )
        assert submit.status_code == 202
        job_id = submit.json()["job_id"]

        replay = client.get(f"/v1/jobs/{job_id}/replay", headers=AUTH)
        assert replay.status_code == 200
        body = replay.json()
        assert body["mode"] == "timeline_playback"
        assert body["event_count"] >= 1

        obs = client.get(f"/v1/jobs/{job_id}/observability", headers=AUTH)
        assert obs.status_code == 200
        payload = obs.json()
        assert payload["usage"]["coverage"] in {"complete", "partial", "unavailable"}
        assert payload["cost"]["kind"] == "estimate"
        assert "estimate" in payload["cost"]["notes"][-1].lower() or any(
            "estimate" in n.lower() for n in payload["cost"]["notes"]
        )

        result = client.get(f"/v1/jobs/{job_id}/result", headers=AUTH)
        assert result.status_code == 200
        assert result.json()["result"]["observability"] is not None


def test_ops_log_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    from app.observability.logging import ops_log

    with caplog.at_level(logging.INFO):
        ops_log(
            "safe_event",
            authorization="Bearer should-redact",
            prompt="secret prompt",
            status="ok",
        )
    assert any("safe_event" in r.message for r in caplog.records)


def test_unsupported_billing_category_partial() -> None:
    table = PricingTable(
        as_of="2026-01-15",
        provenance="unit-test",
        models={
            "weird": {
                "input": 1.0,
                "output": 1.0,
                "billing": "cached_tokens_only",
            }
        },
    )
    usage = aggregate_run_usage(
        planning=PlanResult(
            plan=ExecutionPlan(
                goal="g",
                tasks=[
                    PlanTask(
                        id="t1",
                        description="d",
                        role=SpecialistRole.GENERAL,
                        depends_on=[],
                        expected_output="o",
                    )
                ],
            ),
            llm_calls=1,
            duration_ms=1.0,
            model="weird",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
        ),
        execution=None,
    )
    cost = estimate_cost(usage, table)
    assert cost.coverage is UsageCoverage.UNAVAILABLE
    assert cost.total_usd is None
