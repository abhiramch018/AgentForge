"""Typed chat messages.

Tool calls and tool results are represented here without any provider types.
The runner executes tools. This module only stores the conversation.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class MessageRole(str, Enum):
    """Who produced a chat message."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    """One tool invocation requested by the model."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "name")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class ChatMessage(BaseModel):
    """One chat message exchanged with an LLM."""

    role: MessageRole
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None

    @field_validator("tool_call_id", "tool_name")
    @classmethod
    def optional_text_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @model_validator(mode="after")
    def message_has_required_parts(self) -> "ChatMessage":
        has_text = bool(self.content.strip())
        if self.role in {MessageRole.SYSTEM, MessageRole.USER}:
            if not has_text:
                raise ValueError("content must not be blank")
        elif self.role is MessageRole.ASSISTANT:
            if not has_text and not self.tool_calls:
                raise ValueError("assistant message needs content or tool calls")
        elif self.role is MessageRole.TOOL:
            if self.tool_call_id is None:
                raise ValueError("tool message needs tool_call_id")
            if not has_text:
                raise ValueError("tool message needs content")
        return self
