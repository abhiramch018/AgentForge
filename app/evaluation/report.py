"""Machine-readable JSON + concise Markdown evaluation reports."""

from __future__ import annotations

import json
from pathlib import Path

from app.evaluation.models import EvalReport


def write_reports(report: EvalReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "eval_report.json"
    md_path = output_dir / "eval_report.md"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def render_markdown(report: EvalReport) -> str:
    m = report.manifest
    s = report.summary
    lines: list[str] = [
        "# AgentForge Evaluation Report",
        "",
        f"- **Result kind:** `{s.get('result_kind', m.mode)}`",
        f"- **Dataset:** `{m.dataset_version}`",
        f"- **Mode:** `{m.mode}` (mocked ≠ live model quality)",
        f"- **Model identifier:** `{m.model_identifier}`",
        f"- **Code version:** `{m.code_version or 'unavailable'}`",
        f"- **Started:** {m.started_at}",
        f"- **Finished:** {m.finished_at or 'n/a'}",
        f"- **Sample count (case×config):** {s.get('sample_count')}",
        "",
        "## Limitations",
        "",
    ]
    for item in m.limitations:
        lines.append(f"- {item}")
    for caveat in s.get("caveats", []):
        lines.append(f"- {caveat}")

    lines.extend(["", "## Config comparison", ""])
    configs = s.get("configs") or {}
    for config_id, row in configs.items():
        lines.append(f"### `{config_id}`")
        lines.append("")
        lines.append(f"- n={row.get('n')}")
        lines.append(f"- success_rate={row.get('success_rate')}")
        lines.append(f"- failure_count={row.get('failure_count')} ids={row.get('failed_case_ids')}")
        lines.append(f"- quality_mean={row.get('quality_mean')}")
        lines.append(
            f"- latency_ms P50={row.get('latency_p50_ms')} P95={row.get('latency_p95_ms')}"
        )
        lines.append(f"- usage_coverage={row.get('usage_coverage_counts')}")
        lines.append(f"- cost_coverage={row.get('cost_coverage_counts')}")
        lines.append(
            f"- cost_usd_sum_estimate={row.get('cost_usd_sum_estimate')} "
            "(estimate only; see coverage)"
        )
        lines.append(f"- revisions_sum={row.get('revision_sum')} model_calls_sum={row.get('model_calls_sum')}")
        lines.append("")

    lines.extend(["## Per-case outcomes", ""])
    lines.append("| case | config | success | quality | status | total_ms | usage | cost_usd |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in report.case_outcomes:
        lines.append(
            "| {case} | {cfg} | {ok} | {q} | {st} | {ms} | {u} | {c} |".format(
                case=row.case_id,
                cfg=row.config_id,
                ok=row.success,
                q=("" if row.quality_score is None else f"{row.quality_score:.2f}"),
                st=row.status or row.error or "",
                ms=row.telemetry.total_ms,
                u=row.telemetry.usage_coverage,
                c=row.telemetry.cost_usd,
            )
        )

    lines.extend(["", "## Failure / recovery suite", ""])
    fail = s.get("failure_suite") or {}
    lines.append(
        f"Passed {fail.get('passed')}/{fail.get('n')}; failed ids={fail.get('failed')}"
    )
    lines.append("")
    for item in report.failure_outcomes:
        mark = "PASS" if item.passed else "FAIL"
        lines.append(
            f"- **{mark}** `{item.id}` ({item.category}, {item.enforcement}): {item.detail}"
        )

    cal = s.get("judge_calibration") or {}
    lines.extend(
        [
            "",
            "## Optional judge calibration (proxy)",
            "",
            f"- compared={cal.get('compared')} agreements={cal.get('agreements')} "
            f"rate={cal.get('agreement_rate')}",
            f"- note: {cal.get('note')}",
            "",
            "Judge limitations:",
        ]
    )
    for lim in cal.get("limitations") or []:
        lines.append(f"- {lim}")

    lines.extend(
        [
            "",
            "## What this does **not** claim",
            "",
            "- Live LLM quality or production semantic memory quality",
            "- Statistical significance from small N",
            "- That scripted injection cases prove live-model resistance",
            "",
        ]
    )
    return "\n".join(lines)
