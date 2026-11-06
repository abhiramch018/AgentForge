"""Unit tests for planning Pydantic models."""

import pytest
from pydantic import ValidationError

from app.orchestration import ExecutionPlan, PlanTask, SpecialistRole


def test_plan_task_accepts_valid_fields() -> None:
    task = PlanTask(
        id="  gather_sources  ",
        description="  Collect pricing pages  ",
        role=SpecialistRole.RESEARCHER,
        expected_output="  Source list with notes  ",
    )

    assert task.id == "gather_sources"
    assert task.description == "Collect pricing pages"
    assert task.depends_on == []
    assert task.expected_output == "Source list with notes"


def test_plan_task_rejects_blank_fields() -> None:
    with pytest.raises(ValidationError):
        PlanTask(
            id="   ",
            description="Do work",
            role=SpecialistRole.GENERAL,
            expected_output="Done",
        )

    with pytest.raises(ValidationError):
        PlanTask(
            id="task_1",
            description="Do work",
            role=SpecialistRole.GENERAL,
            depends_on=["  "],
            expected_output="Done",
        )


def test_execution_plan_rejects_empty_tasks() -> None:
    with pytest.raises(ValidationError):
        ExecutionPlan(goal="Ship a summary", tasks=[])


def test_execution_plan_rejects_unknown_role() -> None:
    with pytest.raises(ValidationError):
        PlanTask(
            id="task_1",
            description="Do work",
            role="wizard",  # type: ignore[arg-type]
            expected_output="Done",
        )
