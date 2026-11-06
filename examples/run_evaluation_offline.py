"""Run the M13 offline evaluation suite and print report paths.

Never makes paid API calls.

Windows::

    python -m examples.run_evaluation_offline
    python -m app.evaluation offline --out reports/eval_offline
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.evaluation.harness import run_offline_evaluation
from app.evaluation.report import write_reports


def main() -> None:
    out = Path("reports") / "eval_offline"
    with tempfile.TemporaryDirectory(prefix="agentforge-eval-") as tmp:
        report = run_offline_evaluation(work_root=Path(tmp))
        json_path, md_path = write_reports(report, out)
    print(f"JSON: {json_path.resolve()}")
    print(f"Markdown: {md_path.resolve()}")
    print(f"dataset={report.manifest.dataset_version} mode={report.manifest.mode}")
    print(f"samples={report.summary.get('sample_count')}")
    fail = report.summary.get("failure_suite") or {}
    print(f"failure_suite={fail.get('passed')}/{fail.get('n')} passed")
    for config_id, row in (report.summary.get("configs") or {}).items():
        print(
            f"  {config_id}: success_rate={row.get('success_rate')} "
            f"quality_mean={row.get('quality_mean')} "
            f"p50_ms={row.get('latency_p50_ms')} p95_ms={row.get('latency_p95_ms')}"
        )
    print("Note: mocked results ≠ live model quality. See report limitations.")


if __name__ == "__main__":
    main()
