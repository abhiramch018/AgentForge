"""Structured review models and policy helpers.

Reviewer acceptance means the output appears to satisfy the assignment given
the supplied context. It is **not** proof of factual correctness or
independent verification of real-world claims.
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.llm.base import TokenUsage
from app.orchestration.models import SpecialistRole


class ReviewDecision(str, Enum):
    """Outcome of one structured review."""

    ACCEPTED = "accepted"
    NEEDS_REVISION = "needs_revision"
    CANNOT_ASSESS = "cannot_assess"


class ReviewPolicy(str, Enum):
    """Why a task did or did not go through the quality gate."""

    REVIEWED = "reviewed"
    BYPASSED_REVIEW_DISABLED = "bypassed_review_disabled"
    BYPASSED_ROLE_FILTER = "bypassed_role_filter"


class ReviewResult(BaseModel):
    """Immutable snapshot of one reviewer decision."""

    model_config = ConfigDict(frozen=True)

    decision: ReviewDecision
    feedback: str = Field(min_length=1)
    issues: list[str] = Field(default_factory=list)
    task_coverage: str = Field(min_length=1)
    expected_output_fit: str = Field(min_length=1)
    internal_consistency: str = Field(min_length=1)
    evidence_support: str = Field(min_length=1)
    limitations: str = Field(min_length=1)
    checks_supplied_evidence_only: bool = True
    independently_verified_facts: bool = False
    model: str | None = None
    usage: TokenUsage | None = None
    duration_ms: float = Field(ge=0)
    reviewer_model_calls: int = Field(ge=1)
    reviewer_repair_calls: int = Field(ge=0)

    @field_validator(
        "feedback",
        "task_coverage",
        "expected_output_fit",
        "internal_consistency",
        "evidence_support",
        "limitations",
    )
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    def snapshot(self) -> "ReviewResult":
        """Return a detached copy so later edits cannot alter history."""

        return self.model_copy(deep=True)


class AttemptKind(str, Enum):
    INITIAL = "initial"
    REVISION = "revision"


class AttemptRecord(BaseModel):
    """Immutable snapshot of one specialist attempt and its review."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=0)
    kind: AttemptKind
    output: str | None = None
    error: str | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    model: str | None = None
    usage: TokenUsage | None = None
    review: ReviewResult | None = None

    def snapshot(self) -> "AttemptRecord":
        return self.model_copy(deep=True)


class ReviewSettings(BaseModel):
    """Visible review configuration for one executor."""

    enabled: bool = False
    roles: frozenset[SpecialistRole] | None = None
    max_revisions: int = Field(default=1, ge=0)
    max_review_repair_attempts: int = Field(default=1, ge=0)

    def applies_to(self, role: SpecialistRole) -> bool:
        if not self.enabled:
            return False
        if self.roles is None:
            return True
        return role in self.roles

    def bypass_reason(self, role: SpecialistRole) -> str | None:
        if not self.enabled:
            return "review_disabled"
        if self.roles is not None and role not in self.roles:
            return f"role_filter_excluded:{role.value}"
        return None

    def max_specialist_attempts(self) -> int:
        return 1 + self.max_revisions

    def max_reviewer_model_calls_per_task(self) -> int:
        """Upper bound on reviewer LLM calls for one reviewed task."""

        reviews = self.max_specialist_attempts()
        calls_per_review = 1 + self.max_review_repair_attempts
        return reviews * calls_per_review
