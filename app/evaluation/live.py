"""Opt-in live evaluation — never runs paid calls unless explicitly confirmed.

Usage::

    python -m app.evaluation live --confirm-paid-calls --max-calls 5 ...

Without ``--confirm-paid-calls``, this module refuses to start.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.evaluation.models import EvalReport
from app.evaluation.report import write_reports


class LiveEvalRefused(RuntimeError):
    """Raised when live evaluation is requested without explicit confirmation."""


def run_live_evaluation(
    *,
    confirm_paid_calls: bool,
    max_calls: int,
    max_tokens: int,
    output_dir: Path,
    dataset_version: str = "v1",
) -> EvalReport:
    if not confirm_paid_calls:
        raise LiveEvalRefused(
            "Live evaluation is opt-in. Re-run with --confirm-paid-calls and "
            "explicit --max-calls / --max-tokens budgets. Offline suite: "
            "`python -m app.evaluation offline`."
        )
    if max_calls < 1 or max_tokens < 1:
        raise LiveEvalRefused("Live budgets must be positive.")

    # Intentionally not implemented as an automatic runner: keep paid calls
    # operator-driven. Record the refusal surface for reports/tests.
    raise LiveEvalRefused(
        "Live evaluation scaffolding is present with budgets "
        f"(max_calls={max_calls}, max_tokens={max_tokens}, "
        f"dataset={dataset_version}, out={output_dir}), but this checkout "
        "does not auto-invoke paid providers. Run the offline suite for "
        "reproducible mocked results, or wire a manual live driver under "
        "those budgets."
    )


def live_cli_help() -> dict[str, Any]:
    return {
        "required_flag": "--confirm-paid-calls",
        "budgets": ["--max-calls", "--max-tokens"],
        "default": "refuses; no paid API calls",
        "offline": "python -m app.evaluation offline",
    }
