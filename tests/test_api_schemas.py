"""Contract tests for versioned API schemas."""

import pytest
from pydantic import ValidationError

from app.api.v1.schemas import PlanInput, RunRequest
from app.orchestration import SpecialistRole


def test_run_request_requires_exactly_one_of_goal_or_plan() -> None:
    with pytest.raises(ValidationError):
        RunRequest()
    with pytest.raises(ValidationError):
        RunRequest(goal="Do work", plan=_plan())
    req = RunRequest(goal="Do work")
    assert req.plan is None
    req2 = RunRequest(plan=_plan())
    assert req2.goal is None


def test_run_request_rejects_unknown_fields_and_tool_options() -> None:
    with pytest.raises(ValidationError):
        RunRequest(goal="x", tools=["calculator"])  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        RunRequest(goal="x", extra_field=1)  # type: ignore[call-arg]


def test_plan_input_rejects_cycles() -> None:
    with pytest.raises(ValidationError, match="circular|Invalid"):
        PlanInput(
            goal="g",
            tasks=[
                {
                    "id": "a",
                    "description": "A",
                    "role": "general",
                    "depends_on": ["b"],
                    "expected_output": "A",
                },
                {
                    "id": "b",
                    "description": "B",
                    "role": "general",
                    "depends_on": ["a"],
                    "expected_output": "B",
                },
            ],
        )


def test_review_options_are_bounded() -> None:
    with pytest.raises(ValidationError):
        RunRequest(goal="x", max_revisions=99)
    with pytest.raises(ValidationError):
        RunRequest(goal="x", max_concurrency=0)
    ok = RunRequest(
        goal="x",
        review_enabled=True,
        review_roles=[SpecialistRole.WRITER],
        max_revisions=2,
        max_concurrency=2,
    )
    assert ok.review_roles == [SpecialistRole.WRITER]


def _plan() -> PlanInput:
    return PlanInput(
        goal="Goal",
        tasks=[
            {
                "id": "only",
                "description": "Do it",
                "role": "general",
                "depends_on": [],
                "expected_output": "Done",
            }
        ],
    )
