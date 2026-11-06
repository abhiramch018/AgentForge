"""Deterministic fake LLM for offline demos — never makes paid API calls.

Labeled demonstration provider. Scripted replies are **not** evidence of
real model intelligence or live quality.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.messages import ChatMessage
from app.llm.base import LLMClient, LLMResponse, TokenUsage

# Phrase that unlocks the multi-task vendor portfolio plan (also used by UI preset).
PORTFOLIO_DEMO_MARKER = "PORTFOLIO_VENDOR_COMPARE"


def _usage() -> TokenUsage:
    return TokenUsage(prompt_tokens=40, completion_tokens=25, total_tokens=65)


def _accept_json(*, feedback: str) -> str:
    return (
        '{"decision":"accepted","feedback":'
        + _json_str(feedback)
        + ',"issues":[],"task_coverage":"complete","expected_output_fit":"strong",'
        '"internal_consistency":"ok","evidence_support":"ok",'
        '"limitations":"Demo fake model — assignment-fit only; not factual verification.",'
        '"checks_supplied_evidence_only":true,"independently_verified_facts":false}'
    )


def _revise_json() -> str:
    return (
        '{"decision":"needs_revision","feedback":'
        '"Name the winning vendor explicitly and cite both list prices from the briefs.",'
        '"issues":["Recommendation does not state a clear winner","Missing explicit price comparison"],'
        '"task_coverage":"partial","expected_output_fit":"weak",'
        '"internal_consistency":"ok","evidence_support":"thin",'
        '"limitations":"Demo fake model forced one revision for portfolio demo.",'
        '"checks_supplied_evidence_only":true,"independently_verified_facts":false}'
    )


def _json_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class DemoLLM(LLMClient):
    """Offline demo provider used by M14–M16 stacks."""

    def __init__(self) -> None:
        self.calls = 0
        self._portfolio_review_passes = 0

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        self.calls += 1
        blob = "\n".join(f"{m.role.value}:{m.content}" for m in messages)
        lower = blob.lower()
        portfolio = PORTFOLIO_DEMO_MARKER.lower() in lower or (
            "nordiccloud" in lower and "harborstack" in lower
        )

        if "output reviewer" in lower:
            return self._review(lower, portfolio=portfolio)

        if "planning supervisor" in lower or "lean execution plan" in lower:
            if portfolio:
                return LLMResponse(
                    content=self._portfolio_plan_json(),
                    model="fake-model",
                    usage=_usage(),
                )
            return LLMResponse(
                content=(
                    '{"goal":"Demo plan","summary":"Single-task demo plan",'
                    '"tasks":[{"id":"t1","description":"Produce a short demo answer",'
                    '"role":"general","depends_on":[],'
                    '"expected_output":"One paragraph mentioning AgentForge"}]}'
                ),
                model="fake-model",
                usage=_usage(),
            )

        return LLMResponse(
            content=self._specialist(lower, portfolio=portfolio),
            model="fake-model",
            usage=_usage(),
        )

    def _review(self, lower: str, *, portfolio: bool) -> LLMResponse:
        # Portfolio demo: force one needs_revision on the recommendation task,
        # then accept — exercises bounded revision without claiming live quality.
        if portfolio and "recommend" in lower:
            self._portfolio_review_passes += 1
            if self._portfolio_review_passes == 1:
                return LLMResponse(
                    content=_revise_json(), model="fake-model", usage=_usage()
                )
        return LLMResponse(
            content=_accept_json(
                feedback=(
                    "Demo reviewer: assignment fit OK against supplied task text."
                    if not portfolio
                    else "Demo reviewer: revision addressed winner + prices from briefs."
                )
            ),
            model="fake-model",
            usage=_usage(),
        )

    @staticmethod
    def _portfolio_plan_json() -> str:
        return (
            '{"goal":"Compare NordicCloud vs HarborStack using supplied briefs",'
            '"summary":"Analyze each vendor concurrently, then recommend",'
            '"tasks":['
            '{"id":"analyze_nordic","description":'
            '"Summarize NordicCloud pricing, support, strengths, and weaknesses '
            'using only the NordicCloud brief in the goal context.",'
            '"role":"analyst","depends_on":[],'
            '"expected_output":"Bullet summary citing list price and support"},'
            '{"id":"analyze_harbor","description":'
            '"Summarize HarborStack pricing, support, strengths, and weaknesses '
            'using only the HarborStack brief in the goal context.",'
            '"role":"analyst","depends_on":[],'
            '"expected_output":"Bullet summary citing list price and support"},'
            '{"id":"recommend","description":'
            '"Write a short recommendation choosing one vendor with trade-offs, '
            'citing both list prices from the supplied briefs.",'
            '"role":"writer","depends_on":["analyze_nordic","analyze_harbor"],'
            '"expected_output":"Clear winner, prices, and residual risks"}'
            "]}"
        )

    @staticmethod
    def _specialist(lower: str, *, portfolio: bool) -> str:
        if portfolio and "nordiccloud" in lower and "harborstack" not in lower.split(
            "assignment", 1
        )[-1][:400]:
            # Prefer task-targeted branches
            pass
        if "analyze_nordic" in lower or (
            portfolio and "nordiccloud" in lower and "summarize nordiccloud" in lower
        ):
            return (
                "NordicCloud analysis (supplied brief only): list price $4,200/mo for "
                "40 vCPU / 160 GB; business-hours support with 8h Sev-1 target; strong EU "
                "residency; no multi-region active-active. Fake-model demo text."
            )
        if "analyze_harbor" in lower or (
            portfolio and "summarize harborstack" in lower
        ):
            return (
                "HarborStack analysis (supplied brief only): list price $5,100/mo plus "
                "$600 policy pack; 24×7 Sev-1 with 1h target; multi-region failover; "
                "$8,000 kickoff. Fake-model demo text."
            )
        if portfolio and ("recommend" in lower or "recommendation" in lower):
            # After forced revision feedback, include winner + both prices.
            if "name the winning vendor" in lower or "list prices" in lower:
                return (
                    "Recommendation (supplied briefs only): Choose NordicCloud for this "
                    "mid-size SaaS profile. NordicCloud list $4,200/mo vs HarborStack "
                    "$5,100/mo (+$600 policy). Trade-off: lower cost and EU residency vs "
                    "HarborStack's multi-region and faster Sev-1. Residual risk: NordicCloud "
                    "lacks active-active multi-region. Fake-model demo — not live intelligence."
                )
            return (
                "Preliminary recommendation draft: prefer the lower-cost EU-oriented option "
                "for this profile; expand with explicit prices on revision. Fake-model demo."
            )
        return (
            "AgentForge demo output: planned work completed with a fake model. "
            "This is demonstration mode — not a live provider response."
        )
