"""Provider-neutral LLM client interface."""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from pydantic import BaseModel, Field, model_validator

from app.core.messages import ChatMessage, ToolCall
from app.tools.base import Tool


class TokenUsage(BaseModel):
    """Token counts reported by a provider, when that provider sends them."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class LLMResponse(BaseModel):
    """A normalized model reply: final text, tool calls, or both."""

    content: str | None = None
    model: str = Field(min_length=1)
    usage: TokenUsage | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)

    @model_validator(mode="after")
    def has_text_or_tool_calls(self) -> "LLMResponse":
        has_text = self.content is not None and bool(self.content.strip())
        if not has_text and not self.tool_calls:
            raise ValueError("response needs content or tool calls")
        return self


class LLMClient(ABC):
    """Sends chat messages to an LLM and returns a normalized response.

    ``tools`` is metadata advertised to the model. The client does not
    execute tools.
    """

    @abstractmethod
    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Tool] | None = None,
    ) -> LLMResponse:
        """Generate one assistant reply for ``messages``."""
