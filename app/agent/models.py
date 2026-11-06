"""Input and output models for one agent execution."""

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.agent.exceptions import AgentRequestError, format_validation_error
from app.llm.base import TokenUsage


class AgentRequest(BaseModel):
    """One task for an agent to answer."""

    task: str = Field(min_length=1)

    @field_validator("task")
    @classmethod
    def task_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    def __init__(self, /, **data: object) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            raise AgentRequestError(
                f"Invalid agent request. {format_validation_error(exc)}"
            ) from exc


class AgentResult(BaseModel):
    """The answer produced by one agent execution."""

    final_answer: str
    agent_name: str = Field(min_length=1)
    model: str = Field(min_length=1)
    usage: TokenUsage | None = None
