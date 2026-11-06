"""A named AI worker.

The agent owns its identity, system instruction, and the tool names it is
allowed to use. It builds messages. It does not execute tools.
"""

from collections.abc import Sequence

from app.agent.exceptions import AgentConfigurationError, AgentError
from app.agent.models import AgentRequest, AgentResult
from app.core.messages import ChatMessage, MessageRole
from app.llm.base import LLMClient, LLMResponse


class Agent:
    """Defines how one AI worker behaves."""

    def __init__(
        self,
        name: str,
        system_instruction: str,
        llm_client: LLMClient,
        allowed_tools: Sequence[str] | None = None,
    ) -> None:
        self.name = _require_text(name, "name")
        self.system_instruction = _require_text(
            system_instruction,
            "system_instruction",
        )
        if not isinstance(llm_client, LLMClient):
            raise AgentConfigurationError("llm_client must be an LLMClient.")
        self.llm_client = llm_client
        self.allowed_tools = _tool_names(allowed_tools)

    def build_messages(self, task: str) -> list[ChatMessage]:
        """Build the system and user messages for ``task``."""

        request = AgentRequest(task=task)
        return [
            ChatMessage(role=MessageRole.SYSTEM, content=self.system_instruction),
            ChatMessage(role=MessageRole.USER, content=request.task),
        ]

    def run(self, task: str) -> AgentResult:
        """Answer ``task`` with one generation and no tool execution.

        Agents that list tools must be executed with ``AgentRunner``.
        """

        if self.allowed_tools:
            raise AgentError(
                "This agent has tools. Execute it with AgentRunner so tool "
                "calls can run."
            )
        messages = self.build_messages(task)
        response = self.llm_client.generate(messages)
        return _to_result(self.name, response)


def _require_text(value: str, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise AgentConfigurationError(f"{field_name} must not be blank.")
    return stripped


def _tool_names(names: Sequence[str] | None) -> tuple[str, ...]:
    if not names:
        return ()
    allowed: list[str] = []
    for name in names:
        cleaned = name.strip()
        if not cleaned:
            raise AgentConfigurationError("tool name must not be blank.")
        if cleaned in allowed:
            raise AgentConfigurationError(
                f"tool '{cleaned}' is listed more than once."
            )
        allowed.append(cleaned)
    return tuple(allowed)


def _to_result(agent_name: str, response: LLMResponse) -> AgentResult:
    if response.tool_calls:
        raise AgentError(
            "The model requested a tool. Use AgentRunner to execute tools."
        )
    if response.content is None:
        raise AgentError("The model returned no text.")
    return AgentResult(
        final_answer=response.content,
        agent_name=agent_name,
        model=response.model,
        usage=response.usage,
    )
