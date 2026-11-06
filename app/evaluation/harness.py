"""Offline evaluation harness — mocked by default; live is opt-in."""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.api.concurrency import CapacityGate
from app.api.jobs.events import JobEventStore
from app.api.jobs.models import JobStatus
from app.api.jobs.queue import JobQueue
from app.api.jobs.runtime import JobRuntime, set_runtime
from app.api.jobs.store import JobStore
from app.api.service import OrchestrationService
from app.api.v1.schemas import PlanInput, RunRequest, RunResponse
from app.approval.exceptions import ApprovalRequired
from app.approval.models import ProtectedAction
from app.approval.service import ApprovalService
from app.approval.store import ApprovalStore
from app.evaluation.dataset import cases_for_split, load_dataset
from app.evaluation.failures import run_failure_suite
from app.evaluation.judge import calibrate_against_humans, proxy_judge
from app.evaluation.models import (
    CaseOutcome,
    CaseTelemetry,
    EvalConfig,
    EvalDataset,
    EvalManifest,
    EvalReport,
)
from app.evaluation.providers import EvalScriptedLLM
from app.evaluation.scoring import score_case
from app.evaluation.stats import mean, percentile
from app.llm.base import LLMClient
from app.memory.embeddings import HashingEmbedder
from app.memory.models import MemoryKind, MemoryTrust
from app.memory.service import MemoryService, NullMemoryService
from app.memory.store import MemoryStore
from app.observability.models import ObservabilitySummary

DEFAULT_CONFIGS: list[EvalConfig] = [
    EvalConfig(
        id="review_off_memory_off",
        review_enabled=False,
        memory_enabled=False,
        max_concurrency=1,
        description="Baseline: no review, no memory (concurrency=1 for reproducible scripts)",
    ),
    EvalConfig(
        id="review_on_memory_off",
        review_enabled=True,
        memory_enabled=False,
        max_concurrency=1,
        description="Review on, memory off",
    ),
    EvalConfig(
        id="review_off_memory_on",
        review_enabled=False,
        memory_enabled=True,
        max_concurrency=1,
        description="Review off, hashing memory on",
    ),
    EvalConfig(
        id="review_on_memory_on",
        review_enabled=True,
        memory_enabled=True,
        max_concurrency=1,
        description="Review on, hashing memory on",
    ),
]


def code_version() -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        return out.strip() or None
    except Exception:
        return None


def run_offline_evaluation(
    *,
    dataset_version: str = "v1",
    work_root: Path,
    configs: Sequence[EvalConfig] | None = None,
    include_held_out: bool = True,
    include_failures: bool = True,
    model_identifier: str = "fake-model",
) -> EvalReport:
    """Run the fixed offline suite with fake providers. Never makes paid calls."""

    started = datetime.now(timezone.utc).isoformat()
    dataset = load_dataset(dataset_version)
    selected = cases_for_split(dataset, include_held_out=include_held_out)
    configs = list(configs or DEFAULT_CONFIGS)
    outcomes: list[CaseOutcome] = []
    judge_opinions = []

    for config in configs:
        for case in selected:
            outcome = _run_one_case(
                case,
                config=config,
                work_root=work_root,
                model_identifier=model_identifier,
                retrieval_label=dataset.retrieval_label,
            )
            outcomes.append(outcome)
            judge_opinions.append(proxy_judge(case, outcome.criteria))

    failure_outcomes = run_failure_suite(work_root / "failures") if include_failures else []
    finished = datetime.now(timezone.utc).isoformat()
    calibration = calibrate_against_humans(judge_opinions)

    manifest = EvalManifest(
        dataset_version=dataset.version,
        mode="mocked",
        started_at=started,
        finished_at=finished,
        model_identifier=model_identifier,
        code_version=code_version(),
        configs=configs,
        budgets={"paid_api_calls": 0, "note": "Offline suite uses fake providers only."},
        limitations=[
            dataset.retrieval_label,
            "Mocked results are not live model quality measurements.",
            "Small sample sizes; do not overstate statistical significance.",
            "Optional judge here is a deterministic proxy calibrated against a tiny human set.",
            "Prompt-injection cases prove boundary wiring, not live-model resistance.",
        ],
    )
    summary = build_summary(outcomes, failure_outcomes, calibration=calibration)
    return EvalReport(
        manifest=manifest,
        case_outcomes=outcomes,
        failure_outcomes=failure_outcomes,
        summary=summary,
    )


def build_summary(
    outcomes: Sequence[CaseOutcome],
    failure_outcomes: Sequence[Any],
    *,
    calibration: dict[str, object] | None = None,
) -> dict[str, Any]:
    by_config: dict[str, list[CaseOutcome]] = {}
    for row in outcomes:
        by_config.setdefault(row.config_id, []).append(row)

    config_rows: dict[str, Any] = {}
    for config_id, rows in by_config.items():
        latencies = [
            r.telemetry.total_ms
            for r in rows
            if r.telemetry.total_ms is not None
        ]
        qualities = [r.quality_score for r in rows if r.quality_score is not None]
        failures = [r for r in rows if not r.success]
        config_rows[config_id] = {
            "n": len(rows),
            "success_rate": (len(rows) - len(failures)) / len(rows) if rows else None,
            "failure_count": len(failures),
            "failed_case_ids": [r.case_id for r in failures],
            "quality_mean": mean([q for q in qualities if q is not None]),
            "latency_p50_ms": percentile(latencies, 50),
            "latency_p95_ms": percentile(latencies, 95),
            "usage_coverage_counts": _count_field(rows, "usage_coverage"),
            "cost_coverage_counts": _count_field(rows, "cost_coverage"),
            "cost_usd_sum_estimate": _sum_costs(rows),
            "revision_sum": sum(r.telemetry.revision_count for r in rows),
            "model_calls_sum": sum(
                r.telemetry.model_calls or 0 for r in rows
            ),
        }

    return {
        "result_kind": "mocked_offline",
        "sample_count": len(outcomes),
        "configs": config_rows,
        "failure_suite": {
            "n": len(failure_outcomes),
            "passed": sum(1 for f in failure_outcomes if f.passed),
            "failed": [f.id for f in failure_outcomes if not f.passed],
        },
        "judge_calibration": calibration or {},
        "caveats": [
            "Percentiles from small N are unstable.",
            "Cost totals are fixture estimates when coverage is complete/partial.",
            "Unsuccessful runs are included in rates — not hidden.",
        ],
    }


def _count_field(rows: Sequence[CaseOutcome], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = getattr(row.telemetry, field, None)
        key = str(value) if value is not None else "missing"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _sum_costs(rows: Sequence[CaseOutcome]) -> float | None:
    vals = [r.telemetry.cost_usd for r in rows if r.telemetry.cost_usd is not None]
    if not vals:
        return None
    # Only meaningful when every priced row had coverage; still labeled estimate.
    return sum(vals)


def _run_one_case(
    case: Any,
    *,
    config: EvalConfig,
    work_root: Path,
    model_identifier: str,
    retrieval_label: str,
) -> CaseOutcome:
    case_root = work_root / config.id / case.id
    case_root.mkdir(parents=True, exist_ok=True)
    llm = EvalScriptedLLM(case.scripted_turns, model=model_identifier)

    @contextmanager
    def scope() -> Iterator[LLMClient]:
        yield llm

    approvals = ApprovalService(
        ApprovalStore(case_root / "approvals.db"),
        workspace_id=f"eval-{config.id}",
        artifact_root=str(case_root / "artifacts"),
        ttl_ms=3_600_000,
    )

    memory: MemoryService | NullMemoryService
    if config.memory_enabled:
        # Fresh SQLite + HashingEmbedder per case — prevents cross-case contamination.
        memory = MemoryService(
            MemoryStore(case_root / "memory.db"),
            workspace_id=f"eval-{config.id}-{case.id}",
            retention_days=30.0,
            embedder=HashingEmbedder(),
        )
        embedder = HashingEmbedder()
        for idx, text in enumerate(case.memory_seed):
            memory.store.insert(
                workspace_id=memory.workspace_id,
                kind=MemoryKind.TASK_SUMMARY,
                trust=MemoryTrust.UNREVIEWED,
                summary=text,
                provenance={"run_id": "eval-seed", "seed_index": idx},
                content_hash=f"eval-seed-{case.id}-{idx}",
                embedding=embedder.embed(text),
            )
    else:
        memory = NullMemoryService()

    events = JobEventStore(case_root / "jobs.db", max_events_per_job=200)
    service = OrchestrationService(
        client_scope=scope,
        memory=memory,
        approval=approvals,
        events=events,
    )

    request = _build_request(case, config)
    response: RunResponse | None = None
    error: str | None = None
    approval_required = False
    run_id = str(uuid.uuid4())

    if case.protected_action is not None:
        # Exercise job path so approval can pause without holding HTTP.
        runtime = JobRuntime(
            store=JobStore(case_root / "jobs.db"),
            queue=JobQueue(case_root / "huey.db", immediate=True),
            service=service,
            capacity=CapacityGate(max_runs=2, max_tasks=8),
            max_run_concurrency=4,
            events=events,
            approval=approvals,
            worker_id=f"eval-{case.id}",
        )
        set_runtime(runtime)
        record = runtime.submit(request)
        approval_required = record.status is JobStatus.AWAITING_APPROVAL
        if record.status is JobStatus.FAILED:
            error = record.error_message or "job failed"
        elif record.result is not None and not approval_required:
            response = RunResponse.model_validate(record.result)
        elif record.result is not None and approval_required:
            response = RunResponse.model_validate(record.result)
        artifact_path = case_root / "artifacts" / request.protected_action.relative_path  # type: ignore[union-attr]
        artifact_exists = artifact_path.is_file()
        telemetry = _telemetry_from_response(response, llm_calls=llm.calls)
        if record.timing.total_ms is not None:
            telemetry.total_ms = record.timing.total_ms
        return score_case(
            case,
            config=config,
            response=response,
            error=error,
            approval_required=approval_required,
            artifact_exists=artifact_exists,
            telemetry=telemetry,
            mode="mocked",
        )

    try:
        response = service.run(
            request,
            run_id=run_id,
            concurrency=config.max_concurrency,
        )
    except ApprovalRequired:
        approval_required = True
    except Exception as exc:  # noqa: BLE001 — capture as unsuccessful eval row
        error = f"{type(exc).__name__}: {exc}"

    artifact_exists = False
    telemetry = _telemetry_from_response(response, llm_calls=llm.calls)
    outcome = score_case(
        case,
        config=config,
        response=response,
        error=error,
        approval_required=approval_required,
        artifact_exists=artifact_exists,
        telemetry=telemetry,
        mode="mocked",
    )
    outcome.notes = list(outcome.notes) + [retrieval_label]
    return outcome


def _build_request(case: Any, config: EvalConfig) -> RunRequest:
    action = None
    if case.protected_action is not None:
        action = ProtectedAction.model_validate(case.protected_action)
    if case.plan is not None:
        return RunRequest(
            api_version="v1",
            plan=PlanInput.model_validate(case.plan),
            review_enabled=config.review_enabled,
            max_revisions=config.max_revisions,
            max_concurrency=config.max_concurrency,
            memory_enabled=config.memory_enabled,
            protected_action=action,
        )
    assert case.goal is not None
    return RunRequest(
        api_version="v1",
        goal=case.goal,
        review_enabled=config.review_enabled,
        max_revisions=config.max_revisions,
        max_concurrency=config.max_concurrency,
        memory_enabled=config.memory_enabled,
        protected_action=action,
    )


def _telemetry_from_response(
    response: RunResponse | None,
    *,
    llm_calls: int,
) -> CaseTelemetry:
    if response is None:
        return CaseTelemetry(model_calls=llm_calls)
    obs: ObservabilitySummary | None = response.observability
    revisions = sum(t.revision_count for t in response.execution.task_results)
    tel = CaseTelemetry(
        total_ms=response.timing.total_ms,
        plan_ms=response.timing.plan_ms,
        execute_ms=response.timing.execute_ms,
        model_calls=llm_calls,
        revision_count=revisions,
    )
    if obs is not None:
        tel.prompt_tokens = obs.usage.prompt_tokens
        tel.completion_tokens = obs.usage.completion_tokens
        tel.total_tokens = obs.usage.total_tokens
        tel.usage_coverage = obs.usage.coverage.value
        tel.cost_usd = obs.cost.total_usd
        tel.cost_coverage = obs.cost.coverage.value
        tel.cost_kind = obs.cost.kind
        tel.trace_id = obs.trace_id
    return tel
