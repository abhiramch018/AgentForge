"""A registry of tools owned by one runner.

The registry is not global. An agent still needs its own allow-list; being
registered does not make a tool available to every agent.
"""

from pydantic import BaseModel

from app.tools.base import DuplicateToolError, Tool, ToolError, UnknownToolError


class ToolRegistry:
    """Register, look up, and list tool implementations."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not isinstance(tool, Tool):
            raise ToolError("register() expects a Tool.")
        name = tool.name.strip()
        if not name:
            raise ToolError("Tool name must not be blank.")
        if not isinstance(tool.input_model, type) or not issubclass(
            tool.input_model,
            BaseModel,
        ):
            raise ToolError(f"Tool '{name}' needs a Pydantic input model.")
        if name in self._tools:
            raise DuplicateToolError(f"Tool '{name}' is already registered.")
        self._tools[name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownToolError(f"Unknown tool '{name}'.") from None

    def list_tools(self) -> tuple[Tool, ...]:
        return tuple(self._tools.values())
