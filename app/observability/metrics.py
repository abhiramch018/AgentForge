"""In-process metrics with bounded label cardinality.

Run IDs and task IDs are intentionally not used as labels — keep those in
logs and traces. Metric failures never raise into job execution.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Iterable

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = defaultdict(int)
_histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(
    list
)

# Allowed label keys only — prevents accidental high-cardinality explosion.
_ALLOWED_LABELS = frozenset(
    {
        "status",
        "outcome",
        "phase",
        "decision",
        "exporter",
        "priced",
    }
)


def _norm_labels(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    cleaned = {
        k: v
        for k, v in labels.items()
        if k in _ALLOWED_LABELS and isinstance(v, str) and len(v) < 64
    }
    return tuple(sorted(cleaned.items()))


def incr(name: str, *, labels: dict[str, str] | None = None, amount: int = 1) -> None:
    try:
        key = (name, _norm_labels(labels))
        with _lock:
            _counters[key] += amount
    except Exception:
        logger.exception("metric_incr_failed name=%s", name)


def observe_ms(
    name: str, value_ms: float, *, labels: dict[str, str] | None = None
) -> None:
    try:
        key = (name, _norm_labels(labels))
        with _lock:
            bucket = _histograms[key]
            bucket.append(float(value_ms))
            # Bound memory per series
            if len(bucket) > 1000:
                del bucket[:500]
    except Exception:
        logger.exception("metric_observe_failed name=%s", name)


def snapshot() -> dict[str, object]:
    with _lock:
        return {
            "counters": {
                f"{name}|{dict(labels)}": value
                for (name, labels), value in _counters.items()
            },
            "histograms": {
                f"{name}|{dict(labels)}": {
                    "count": len(values),
                    "sum_ms": sum(values) if values else 0.0,
                }
                for (name, labels), values in _histograms.items()
            },
        }


def reset_metrics_for_tests() -> None:
    with _lock:
        _counters.clear()
        _histograms.clear()


def known_metric_names() -> Iterable[str]:
    return (
        "jobs_submitted",
        "jobs_completed",
        "jobs_failed",
        "queue_wait_ms",
        "execution_ms",
        "approval_wait_ms",
        "review_decisions",
        "revisions",
        "timeouts",
        "retries",
        "capacity_deferred",
    )
