"""Configurable, dated pricing — estimates only, never invented live rates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.observability.models import CostEstimate, CostLine, UsageCoverage, UsageSummary

_DEFAULT_FIXTURE = Path(__file__).with_name("pricing_fixtures.json")


class ModelPrice(BaseModel):
    input: float = Field(ge=0, description="USD per 1M input tokens")
    output: float = Field(ge=0, description="USD per 1M output tokens")
    billing: str = "standard_input_output_tokens"


class PricingTable(BaseModel):
    as_of: str
    currency: str = "USD"
    unit: str = "usd_per_1m_tokens"
    provenance: str
    models: dict[str, ModelPrice]


def load_fixture_pricing(path: str | Path | None = None) -> PricingTable:
    target = Path(path) if path is not None else _DEFAULT_FIXTURE
    raw = json.loads(target.read_text(encoding="utf-8"))
    return PricingTable.model_validate(raw)


def estimate_cost(
    usage: UsageSummary,
    pricing: PricingTable,
) -> CostEstimate:
    """Estimate USD cost from usage + fixture pricing.

    Unknown models yield an unavailable/partial estimate — never a fake total.
    """

    # Aggregate tokens by model from buckets that reported usage.
    by_model: dict[str, dict[str, int | None]] = {}
    unpriced_models: set[str] = set()
    missing_model = False

    for bucket in usage.buckets:
        if bucket.calls_with_usage == 0:
            continue
        model = bucket.model
        if not model:
            missing_model = True
            continue
        slot = by_model.setdefault(
            model, {"prompt": None, "completion": None}
        )
        if bucket.prompt_tokens is not None:
            slot["prompt"] = (slot["prompt"] or 0) + bucket.prompt_tokens
        if bucket.completion_tokens is not None:
            slot["completion"] = (slot["completion"] or 0) + bucket.completion_tokens

    lines: list[CostLine] = []
    notes = list(usage.notes)
    total = 0.0
    any_priced = False
    any_unpriced = False

    for model, toks in by_model.items():
        price = pricing.models.get(model)
        prompt = toks["prompt"]
        completion = toks["completion"]
        if price is None:
            unpriced_models.add(model)
            any_unpriced = True
            lines.append(
                CostLine(
                    model=model,
                    prompt_tokens=prompt if isinstance(prompt, int) else None,
                    completion_tokens=completion if isinstance(completion, int) else None,
                    priced=False,
                )
            )
            continue
        if price.billing != "standard_input_output_tokens":
            any_unpriced = True
            lines.append(
                CostLine(
                    model=model,
                    prompt_tokens=prompt if isinstance(prompt, int) else None,
                    completion_tokens=completion if isinstance(completion, int) else None,
                    priced=False,
                )
            )
            notes.append(
                f"Model {model} uses billing category '{price.billing}' "
                "not supported by this estimator."
            )
            continue
        input_usd = None
        output_usd = None
        line_usd = None
        if isinstance(prompt, int):
            input_usd = (prompt / 1_000_000.0) * price.input
        if isinstance(completion, int):
            output_usd = (completion / 1_000_000.0) * price.output
        if input_usd is not None or output_usd is not None:
            line_usd = (input_usd or 0.0) + (output_usd or 0.0)
            total += line_usd
            any_priced = True
        lines.append(
            CostLine(
                model=model,
                prompt_tokens=prompt if isinstance(prompt, int) else None,
                completion_tokens=completion if isinstance(completion, int) else None,
                input_usd=input_usd,
                output_usd=output_usd,
                line_usd=line_usd,
                priced=True,
            )
        )

    if unpriced_models:
        notes.append(
            "Unpriced models: "
            + ", ".join(sorted(unpriced_models))
            + " — no invented rates."
        )
    if missing_model:
        notes.append("Some usage rows lacked a model name.")
    notes.append(
        f"Pricing as_of={pricing.as_of}; {pricing.provenance}"
    )
    notes.append("Cost figures are estimates, not invoices.")

    if not any_priced and (any_unpriced or usage.coverage is UsageCoverage.UNAVAILABLE):
        coverage = UsageCoverage.UNAVAILABLE
        total_usd = None
    elif (
        any_unpriced
        or usage.coverage is UsageCoverage.PARTIAL
        or missing_model
    ):
        coverage = UsageCoverage.PARTIAL
        total_usd = total if any_priced else None
    else:
        coverage = UsageCoverage.COMPLETE
        total_usd = total

    return CostEstimate(
        currency=pricing.currency,
        as_of=pricing.as_of,
        provenance=pricing.provenance,
        unit=pricing.unit,
        coverage=coverage,
        total_usd=total_usd,
        lines=lines,
        notes=notes,
    )
