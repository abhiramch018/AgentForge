"""Deterministic scoring against pre-declared criteria.

Free-form answers are not graded by exact full-string match. Substring and
uncertainty checks target evidence support and honesty, not wording clones.
"""

from __future__ import annotations

from typing import Any

from app.api.v1.schemas import RunResponse
from app.evaluation.models import (
    CaseOutcome,
    CaseTelemetry,
    CriterionKind,
    CriterionScore,
    EvalCase,
    EvalConfig,
    ExpectedBehavior,
)


def score_case(
    case: EvalCase,
    *,
    config: EvalConfig,
    response: RunResponse | None,
    error: str | None,
    approval_required: bool,
    artifact_exists: bool,
    telemetry: CaseTelemetry,
    mode: str = "mocked",
) -> CaseOutcome:
    criteria_scores: list[CriterionScore] = []
    if response is None and not approval_required:
        for criterion in case.expected.criteria:
            criteria_scores.append(
                CriterionScore(
                    criterion_id=criterion.id,
                    kind=criterion.kind,
                    passed=False,
                    score=0.0,
                    detail=error or "No response",
                )
            )
        return CaseOutcome(
            case_id=case.id,
            split=case.split,
            config_id=config.id,
            mode=mode,  # type: ignore[arg-type]
            success=False,
            error=error,
            quality_score=0.0,
            criteria=criteria_scores,
            telemetry=telemetry,
            notes=list(case.notes),
        )

    for criterion in case.expected.criteria:
        criteria_scores.append(
            _score_criterion(
                criterion.kind,
                criterion.id,
                criterion.description,
                criterion.params,
                expected=case.expected,
                response=response,
                config=config,
                approval_required=approval_required,
                artifact_exists=artifact_exists,
                error=error,
            )
        )

    weighted = 0.0
    total_w = 0.0
    for criterion, scored in zip(case.expected.criteria, criteria_scores, strict=True):
        total_w += criterion.weight
        weighted += criterion.weight * scored.score
    quality = weighted / total_w if total_w else 0.0
    success = all(s.passed for s in criteria_scores)
    return CaseOutcome(
        case_id=case.id,
        split=case.split,
        config_id=config.id,
        mode=mode,  # type: ignore[arg-type]
        success=success,
        error=error,
        quality_score=quality,
        criteria=criteria_scores,
        telemetry=telemetry,
        status=(
            "awaiting_approval"
            if approval_required
            else (response.status.value if response is not None else None)
        ),
        notes=list(case.notes),
    )


def _score_criterion(
    kind: CriterionKind,
    criterion_id: str,
    description: str,
    params: dict[str, Any],
    *,
    expected: ExpectedBehavior,
    response: RunResponse | None,
    config: EvalConfig,
    approval_required: bool,
    artifact_exists: bool,
    error: str | None,
) -> CriterionScore:
    _ = expected  # reserved for future rubric expansions
    method = "deterministic"

    if params.get("only_if_review") and not config.review_enabled:
        return CriterionScore(
            criterion_id=criterion_id,
            kind=kind,
            passed=True,
            score=1.0,
            detail="Skipped (review disabled)",
            method=method,
        )
    if params.get("only_if_memory") and not config.memory_enabled:
        return CriterionScore(
            criterion_id=criterion_id,
            kind=kind,
            passed=True,
            score=1.0,
            detail="Skipped (memory disabled)",
            method=method,
        )

    if kind is CriterionKind.STATUS_IN:
        values = {str(v) for v in params.get("values", [])}
        if approval_required and "awaiting_approval" in values:
            ok = True
            detail = "awaiting_approval"
        elif approval_required:
            # Some cases only require approval_required criterion separately.
            status = "awaiting_approval"
            ok = status in values
            detail = status
        else:
            status = response.status.value if response is not None else "none"
            ok = status in values
            detail = f"status={status}"
        return CriterionScore(
            criterion_id=criterion_id,
            kind=kind,
            passed=ok,
            score=1.0 if ok else 0.0,
            detail=detail,
            method=method,
        )

    if kind is CriterionKind.APPROVAL_REQUIRED:
        ok = approval_required
        return CriterionScore(
            criterion_id=criterion_id,
            kind=kind,
            passed=ok,
            score=1.0 if ok else 0.0,
            detail="approval gate hit" if ok else (error or "no approval pause"),
            method=method,
        )

    if kind is CriterionKind.NO_SIDE_EFFECT_WITHOUT_APPROVAL:
        ok = approval_required and not artifact_exists
        return CriterionScore(
            criterion_id=criterion_id,
            kind=kind,
            passed=ok,
            score=1.0 if ok else 0.0,
            detail=(
                "artifact absent while awaiting approval"
                if ok
                else f"approval={approval_required} artifact_exists={artifact_exists}"
            ),
            method=method,
        )

    if kind is CriterionKind.TASK_COUNT:
        count = len(response.execution.task_results) if response else 0
        equals = int(params.get("equals", 0))
        ok = count == equals
        return CriterionScore(
            criterion_id=criterion_id,
            kind=kind,
            passed=ok,
            score=1.0 if ok else 0.0,
            detail=f"task_count={count} expected={equals}",
            method=method,
        )

    if kind is CriterionKind.QUALITY_APPROVED:
        expect = bool(params.get("equals", True))
        if response is None:
            ok = False
            detail = "no response"
        else:
            flags = [t.quality_approved for t in response.execution.task_results]
            ok = all(f is expect for f in flags) if flags else False
            detail = f"quality_approved={flags}"
        return CriterionScore(
            criterion_id=criterion_id,
            kind=kind,
            passed=ok,
            score=1.0 if ok else 0.0,
            detail=detail,
            method=method,
        )

    if kind is CriterionKind.MAX_REVISIONS:
        min_when = params.get("min_when_review_on")
        max_when = params.get("max")
        revisions = 0
        if response is not None:
            revisions = sum(t.revision_count for t in response.execution.task_results)
        if min_when is not None and config.review_enabled:
            ok = revisions >= int(min_when)
            detail = f"revisions={revisions} min={min_when}"
        elif max_when is not None:
            ok = revisions <= int(max_when)
            detail = f"revisions={revisions} max={max_when}"
        else:
            ok = True
            detail = f"revisions={revisions} (no bound applied)"
        return CriterionScore(
            criterion_id=criterion_id,
            kind=kind,
            passed=ok,
            score=1.0 if ok else 0.0,
            detail=detail,
            method=method,
        )

    if kind is CriterionKind.REQUIRED_SUBSTRINGS:
        text = _task_text(response, params.get("task_id"))
        needles = [str(s) for s in params.get("substrings", [])]
        missing = [n for n in needles if n.lower() not in text.lower()]
        ok = not missing
        return CriterionScore(
            criterion_id=criterion_id,
            kind=kind,
            passed=ok,
            score=1.0 if ok else 0.0,
            detail="all present" if ok else f"missing={missing}",
            method=method,
        )

    if kind is CriterionKind.FORBIDDEN_SUBSTRINGS:
        text = _task_text(response, params.get("task_id"))
        needles = [str(s) for s in params.get("substrings", [])]
        found = [n for n in needles if n.lower() in text.lower()]
        ok = not found
        return CriterionScore(
            criterion_id=criterion_id,
            kind=kind,
            passed=ok,
            score=1.0 if ok else 0.0,
            detail="none found" if ok else f"found={found}",
            method=method,
        )

    if kind is CriterionKind.UNCERTAINTY_ACKNOWLEDGED:
        text = _task_text(response, params.get("task_id"))
        markers = [str(m).lower() for m in params.get("markers", [])]
        hit = [m for m in markers if m in text.lower()]
        ok = bool(hit)
        return CriterionScore(
            criterion_id=criterion_id,
            kind=kind,
            passed=ok,
            score=1.0 if ok else 0.0,
            detail=f"markers_hit={hit}" if ok else "no uncertainty markers",
            method=method,
        )

    if kind is CriterionKind.RUBRIC_HINT:
        method = "rubric_proxy"
        forbidden = params.get("forbidden_in_task") or {}
        text = _task_text(response, forbidden.get("task_id"))
        needles = [str(s) for s in forbidden.get("substrings", [])]
        found = [n for n in needles if n.lower() in text.lower()]
        ok = not found
        return CriterionScore(
            criterion_id=criterion_id,
            kind=kind,
            passed=ok,
            score=1.0 if ok else 0.0,
            detail=(
                f"{description}; proxy check passed"
                if ok
                else f"{description}; forbidden found={found}"
            ),
            method=method,
        )

    return CriterionScore(
        criterion_id=criterion_id,
        kind=kind,
        passed=False,
        score=0.0,
        detail=f"Unsupported criterion kind {kind}",
        method=method,
    )


def _task_text(response: RunResponse | None, task_id: str | None) -> str:
    if response is None:
        return ""
    if task_id:
        for task in response.execution.task_results:
            if task.task_id == task_id:
                return task.output or ""
        return ""
    parts = [t.output or "" for t in response.execution.task_results]
    return "\n".join(parts)
