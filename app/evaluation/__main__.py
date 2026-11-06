"""CLI: ``python -m app.evaluation offline|live|regen``."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from app.evaluation.harness import run_offline_evaluation
from app.evaluation.live import LiveEvalRefused, run_live_evaluation
from app.evaluation.models import EvalReport
from app.evaluation.report import render_markdown, write_reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation",
        description="AgentForge evaluation harness (offline by default; live opt-in).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    offline = sub.add_parser("offline", help="Run mocked offline suite (no paid calls)")
    offline.add_argument(
        "--out",
        type=Path,
        default=Path("reports/eval_offline"),
        help="Directory for eval_report.json and eval_report.md",
    )
    offline.add_argument("--dataset", default="v1")
    offline.add_argument(
        "--skip-held-out",
        action="store_true",
        help="Exclude held-out cases (dev split only)",
    )
    offline.add_argument(
        "--skip-failures",
        action="store_true",
        help="Skip failure/recovery suite",
    )
    offline.add_argument(
        "--work",
        type=Path,
        default=None,
        help="Work directory for per-case isolation (default: temp)",
    )

    live = sub.add_parser(
        "live",
        help="Opt-in live evaluation (refuses without --confirm-paid-calls)",
    )
    live.add_argument("--confirm-paid-calls", action="store_true")
    live.add_argument("--max-calls", type=int, default=0)
    live.add_argument("--max-tokens", type=int, default=0)
    live.add_argument("--out", type=Path, default=Path("reports/eval_live"))
    live.add_argument("--dataset", default="v1")

    regen = sub.add_parser(
        "regen",
        help="Regenerate Markdown from an existing eval_report.json",
    )
    regen.add_argument(
        "json_path",
        type=Path,
        help="Path to eval_report.json",
    )
    regen.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Markdown output path (default: alongside JSON)",
    )

    args = parser.parse_args(argv)

    if args.command == "offline":
        work = args.work
        tmp_ctx = None
        if work is None:
            tmp_ctx = tempfile.TemporaryDirectory(prefix="agentforge-eval-")
            work = Path(tmp_ctx.name)
        try:
            report = run_offline_evaluation(
                dataset_version=args.dataset,
                work_root=Path(work),
                include_held_out=not args.skip_held_out,
                include_failures=not args.skip_failures,
            )
            json_path, md_path = write_reports(report, Path(args.out))
            print(f"Wrote {json_path}")
            print(f"Wrote {md_path}")
            print(
                f"samples={report.summary.get('sample_count')} "
                f"failure_suite_passed="
                f"{(report.summary.get('failure_suite') or {}).get('passed')}/"
                f"{(report.summary.get('failure_suite') or {}).get('n')}"
            )
            return 0
        finally:
            if tmp_ctx is not None:
                tmp_ctx.cleanup()

    if args.command == "live":
        try:
            run_live_evaluation(
                confirm_paid_calls=args.confirm_paid_calls,
                max_calls=args.max_calls,
                max_tokens=args.max_tokens,
                output_dir=Path(args.out),
                dataset_version=args.dataset,
            )
        except LiveEvalRefused as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0

    if args.command == "regen":
        payload = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
        report = EvalReport.model_validate(payload)
        md = render_markdown(report)
        out = args.out or Path(args.json_path).with_suffix(".md")
        out.write_text(md, encoding="utf-8")
        print(f"Wrote {out}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
