"""Small-sample statistics helpers — no overstated significance."""

from __future__ import annotations

from collections.abc import Sequence


def percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    if not 0.0 <= p <= 100.0:
        raise ValueError("percentile must be in [0, 100]")
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (p / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] * (1.0 - frac) + ordered[high] * frac


def mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)
