"""M13 evaluation harness — deterministic offline regression checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.evaluation.dataset import held_out_ids, load_dataset
from app.evaluation.harness import DEFAULT_CONFIGS, run_offline_evaluation
from app.evaluation.judge import calibrate_against_humans, load_human_labels, proxy_judge
from app.evaluation.live import LiveEvalRefused, run_live_evaluation
from app.evaluation.models import CaseSplit, CriterionScore, EvalCase, ExpectedBehavior
from app.evaluation.report import render_markdown, write_reports
from app.evaluation.scoring import score_case
from app.evaluation.stats import percentile
from app.evaluation.__main__ import main as eval_main


def test_dataset_v1_has_dev_and_held_out() -> None:
    dataset = load_dataset("v1")
    assert dataset.version == "v1"
    assert "HashingEmbedder" in dataset.retrieval_label
    splits = {c.split for c in dataset.cases}
    assert CaseSplit.DEV in splits
    assert CaseSplit.HELD_OUT in splits
    held = held_out_ids(dataset)
    assert "heldout_evidence" in held
    assert "heldout_uncertainty" in held
    # Criteria exist before any run.
    for case in dataset.cases:
        assert case.expected.criteria
        assert case.expected.summary


def test_percentile_helpers() -> None:
    assert percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)
    assert percentile([], 50) is None


def test_live_eval_refuses_without_confirmation(tmp_path: Path) -> None:
    with pytest.raises(LiveEvalRefused):
        run_live_evaluation(
            confirm_paid_calls=False,
            max_calls=10,
            max_tokens=1000,
            output_dir=tmp_path,
        )
    with pytest.raises(LiveEvalRefused):
        run_live_evaluation(
            confirm_paid_calls=True,
            max_calls=5,
            max_tokens=1000,
            output_dir=tmp_path,
        )


def test_offline_evaluation_produces_reports(tmp_path: Path) -> None:
    work = tmp_path / "work"
    out = tmp_path / "out"
    # Use a single config for speed in unit regression; full matrix in CLI demo.
    report = run_offline_evaluation(
        work_root=work,
        configs=[DEFAULT_CONFIGS[0], DEFAULT_CONFIGS[1]],
        include_held_out=True,
        include_failures=True,
    )
    assert report.manifest.mode == "mocked"
    assert report.manifest.dataset_version == "v1"
    assert report.manifest.budgets.get("paid_api_calls") == 0
    assert report.case_outcomes
    assert report.failure_outcomes
    assert all(f.passed for f in report.failure_outcomes)

    json_path, md_path = write_reports(report, out)
    assert json_path.is_file()
    assert md_path.is_file()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["summary"]["sample_count"] == len(report.case_outcomes)
    md = md_path.read_text(encoding="utf-8")
    assert "mocked" in md.lower()
    assert "P50" in md or "p50" in md.lower() or "latency_ms P50" in md

    # Regenerate markdown from JSON.
    regenerated = render_markdown(report)
    assert "AgentForge Evaluation Report" in regenerated


def test_config_comparison_isolates_memory(tmp_path: Path) -> None:
    report = run_offline_evaluation(
        work_root=tmp_path / "work",
        configs=[DEFAULT_CONFIGS[0], DEFAULT_CONFIGS[2]],
        include_held_out=False,
        include_failures=False,
    )
    by_cfg = {c.id: c for c in report.manifest.configs}
    assert by_cfg["review_off_memory_off"].memory_enabled is False
    assert by_cfg["review_off_memory_on"].memory_enabled is True
    # Unsuccessful runs remain visible in summary.
    for row in report.summary["configs"].values():
        assert "failure_count" in row
        assert "failed_case_ids" in row


def test_judge_proxy_is_not_sole_truth() -> None:
    labels = load_human_labels()
    assert labels
    case = EvalCase(
        id="incomplete_info",
        split=CaseSplit.DEV,
        title="t",
        expected=ExpectedBehavior(summary="s", criteria=[]),
    )
    from app.evaluation.models import CriterionKind

    opinion = proxy_judge(
        case,
        [
            CriterionScore(
                criterion_id="x",
                kind=CriterionKind.STATUS_IN,
                passed=True,
                score=1.0,
                detail="ok",
            )
        ],
    )
    cal = calibrate_against_humans([opinion], labels)
    assert "limitations" in cal
    assert cal["compared"] >= 1


def test_cli_offline_and_regen(tmp_path: Path) -> None:
    out = tmp_path / "reports"
    work = tmp_path / "work"
    code = eval_main(
        [
            "offline",
            "--out",
            str(out),
            "--work",
            str(work),
            "--skip-held-out",
            "--skip-failures",
        ]
    )
    # Full offline with failures is covered elsewhere; here skip for speed but
    # still exercise CLI wiring. Re-run with failures via harness test.
    assert code == 0
    assert (out / "eval_report.json").is_file()
    regen = eval_main(["regen", str(out / "eval_report.json")])
    assert regen == 0

    live_code = eval_main(["live"])
    assert live_code == 2


def test_scoring_does_not_require_exact_wording() -> None:
    from app.api.v1.schemas import RunResponse, RunTiming
    from app.evaluation.models import (
        CaseTelemetry,
        CriterionKind,
        EvalConfig,
        EvalCriterion,
    )
    from app.orchestration.execution import (
        ExecutionResult,
        ExecutionStatus,
        TaskResult,
        TaskStatus,
    )
    from app.orchestration.models import ExecutionPlan, PlanTask, SpecialistRole

    case = EvalCase(
        id="x",
        split=CaseSplit.DEV,
        title="t",
        expected=ExpectedBehavior(
            summary="evidence",
            criteria=[
                EvalCriterion(
                    id="sku",
                    kind=CriterionKind.REQUIRED_SUBSTRINGS,
                    description="must mention SKU token",
                    params={"task_id": "t1", "substrings": ["W-42"]},
                )
            ],
        ),
    )
    response = RunResponse(
        run_id="r",
        status=ExecutionStatus.SUCCEEDED,
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
        planning=None,
        execution=ExecutionResult(
            goal="g",
            status=ExecutionStatus.SUCCEEDED,
            task_results=[
                TaskResult(
                    task_id="t1",
                    role="general",
                    status=TaskStatus.SUCCEEDED,
                    output="The supplied evidence lists SKU W-42 clearly.",
                )
            ],
            duration_ms=1,
            succeeded_count=1,
            failed_count=0,
            timed_out_count=0,
            skipped_count=0,
        ),
        timing=RunTiming(total_ms=1.0, execute_ms=1.0),
        effective_max_concurrency=1,
        memory_enabled=False,
    )
    outcome = score_case(
        case,
        config=EvalConfig(id="c", review_enabled=False, memory_enabled=False),
        response=response,
        error=None,
        approval_required=False,
        artifact_exists=False,
        telemetry=CaseTelemetry(),
    )
    assert outcome.success is True
