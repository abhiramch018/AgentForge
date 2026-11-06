"""Structured execution plans produced by the supervisor.

These models describe work to do later. Nothing here executes a task.
"""

from enum import Enum

from pydantic import BaseModel, Field, field_validator

from app.llm.base import TokenUsage


class SpecialistRole(str, Enum):
    """Closed set of specialist roles the supervisor may assign.

    Later milestones map each role to a concrete agent. Free-form role
    strings are rejected so the planner cannot invent workers that do not
    exist yet.
    """

    RESEARCHER = "researcher"
    WRITER = "writer"
    ANALYST = "analyst"
    CODER = "coder"
    REVIEWER = "reviewer"
    GENERAL = "general"


class PlanTask(BaseModel):
    """One unit of work in an execution plan."""

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    role: SpecialistRole
    depends_on: list[str] = Field(default_factory=list)
    expected_output: str = Field(min_length=1)

    @field_validator("id", "description", "expected_output")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("depends_on")
    @classmethod
    def dependency_names_must_not_be_blank(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            name = item.strip()
            if not name:
                raise ValueError("dependency id must not be blank")
            cleaned.append(name)
        return cleaned


class ExecutionPlan(BaseModel):
    """A validated DAG of tasks for a single user goal.

    ``goal`` is always the caller's request. The model may suggest a goal
    field; the supervisor overwrites it with the validated input.
    """

    goal: str = Field(min_length=1)
    tasks: list[PlanTask] = Field(min_length=1)
    summary: str | None = None

    @field_validator("goal")
    @classmethod
    def goal_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("summary")
    @classmethod
    def summary_must_not_be_blank_when_set(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class PlanResult(BaseModel):
    """A validated plan plus measured planning metadata.

    Token and cost fields are copied from the LLM client when present.
    Missing provider data stays ``None``; values are never invented.
    """

    plan: ExecutionPlan
    model: str | None = None
    usage: TokenUsage | None = None
    llm_calls: int = Field(ge=1)
    duration_ms: float = Field(ge=0)
    repaired: bool = False
