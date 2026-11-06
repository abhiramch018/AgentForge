"""Provider-independent tool contract.

A tool describes itself and runs one operation. It does not know which LLM
asked for it. Permission rules can be added later; today an agent receives
an explicit allow-list instead of every registered tool.
"""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, model_validator


class ToolError(Exception):
    """Base class for tool registration and lookup failures."""


class DuplicateToolError(ToolError):
    """A tool name was registered twice in the same registry."""


class UnknownToolError(ToolError):
    """No tool with this name is registered."""


class ToolResult(BaseModel):
    """The outcome of one tool execution."""

    success: bool
    output: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def result_matches_status(self) -> "ToolResult":
        if self.success and (self.output is None or not self.output.strip()):
            raise ValueError("a successful tool result needs output")
        if not self.success and (self.error is None or not self.error.strip()):
            raise ValueError("a failed tool result needs an error")
        return self


class Tool(ABC):
    """One operation the runner can execute for a model."""

    name: str
    description: str
    input_model: type[BaseModel]

    @abstractmethod
    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        """Run the tool.

        Expected problems, such as invalid input, should come back as a
        failed ``ToolResult``. Unexpected bugs may still raise.
        """
