"""OpenAI Chat Completions client.

This is the only provider implementation. ``create_llm_client`` is the place
that selects it, so later providers can be added behind ``LLMClient``.

Tool schemas are translated into OpenAI's request shape here. Tool execution
stays in the agent runner.
"""

import json
from collections.abc import Sequence
from typing import Any

from openai import APITimeoutError, OpenAI, OpenAIError
from openai.types.chat import ChatCompletion

from app.core.config import Settings
from app.core.exceptions import (
    LLMConfigurationError,
    LLMProviderError,
    LLMTimeoutError,
)
from app.core.messages import ChatMessage, MessageRole, ToolCall
from app.llm.base import LLMClient, LLMResponse, TokenUsage
from app.tools.base import Tool


class OpenAIChatClient(LLMClient):
    """Send chat messages to OpenAI and normalize the reply."""

    def __init__(self, settings: Settings) -> None:
        if settings.llm_provider != "openai":
            raise LLMConfigurationError(
                "OpenAIChatClient requires llm_provider 'openai', "
                f"got '{settings.llm_provider}'."
            )
        self._settings = settings
        self._sdk = OpenAI(
            api_key=settings.llm_api_key.get_secret_value(),
            timeout=settings.llm_timeout_seconds,
            max_retries=0,
        )

    def close(self) -> None:
        """Release transport resources after all calls have finished."""
        self._sdk.close()

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Tool] | None = None,
    ) -> LLMResponse:
        if len(messages) == 0:
            raise LLMProviderError("At least one message is required.")
        completion = self._create_completion(messages, tools or ())
        return _normalize(completion)

    def _create_completion(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Tool],
    ) -> ChatCompletion:
        request: dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": [_to_provider_message(message) for message in messages],
            "max_completion_tokens": self._settings.llm_max_output_tokens,
        }
        if tools:
            request["tools"] = [_to_provider_tool(tool) for tool in tools]
        try:
            return self._sdk.chat.completions.create(**request)
        except APITimeoutError as exc:
            raise LLMTimeoutError(
                "The LLM request timed out after "
                f"{self._settings.llm_timeout_seconds} seconds."
            ) from exc
        except OpenAIError as exc:
            raise LLMProviderError("The LLM provider request failed.") from exc


def create_llm_client(settings: Settings) -> LLMClient:
    """Build the client named by ``settings.llm_provider``."""

    if settings.llm_provider == "openai":
        return OpenAIChatClient(settings)
    raise LLMConfigurationError(
        f"No LLM client is implemented for provider '{settings.llm_provider}'."
    )


def _to_provider_message(message: ChatMessage) -> dict[str, Any]:
    if message.role is MessageRole.TOOL:
        if message.tool_call_id is None:
            raise LLMProviderError("A tool message needs a tool_call_id.")
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }
    if message.role is MessageRole.ASSISTANT and message.tool_calls:
        return {
            "role": "assistant",
            "content": message.content if message.content.strip() else None,
            "tool_calls": [_to_provider_tool_call(call) for call in message.tool_calls],
        }
    return {"role": message.role.value, "content": message.content}


def _to_provider_tool_call(call: ToolCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": json.dumps(call.arguments),
        },
    }


def _to_provider_tool(tool: Tool) -> dict[str, Any]:
    parameters = tool.input_model.model_json_schema()
    parameters.pop("title", None)
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": parameters,
        },
    }


def _normalize(completion: ChatCompletion) -> LLMResponse:
    if not completion.choices:
        raise LLMProviderError("The LLM provider returned no choices.")
    message = completion.choices[0].message
    tool_calls = _read_tool_calls(message)
    content = _read_text(message)
    if content is None and not tool_calls:
        raise LLMProviderError("The LLM provider returned no text content.")
    return LLMResponse(
        content=content,
        model=completion.model,
        usage=_read_usage(completion),
        tool_calls=tool_calls,
    )


def _read_text(message: Any) -> str | None:
    content = message.content
    if not isinstance(content, str) or not content.strip():
        return None
    return content


def _read_tool_calls(message: Any) -> list[ToolCall]:
    raw_calls = getattr(message, "tool_calls", None)
    if not isinstance(raw_calls, list):
        return []
    return [_read_tool_call(raw_call) for raw_call in raw_calls]


def _read_tool_call(raw_call: Any) -> ToolCall:
    function = getattr(raw_call, "function", None)
    call_id = getattr(raw_call, "id", None)
    name = getattr(function, "name", None)
    raw_arguments = getattr(function, "arguments", None)
    if not isinstance(call_id, str) or not isinstance(name, str):
        raise LLMProviderError("The provider returned a tool call the client cannot read.")
    if not isinstance(raw_arguments, str):
        raise LLMProviderError("The provider returned tool arguments the client cannot read.")
    return ToolCall(id=call_id, name=name, arguments=_parse_arguments(raw_arguments))


def _parse_arguments(raw_arguments: str) -> dict[str, Any]:
    if not raw_arguments.strip():
        return {}
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise LLMProviderError("Tool call arguments were not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise LLMProviderError("Tool call arguments must be a JSON object.")
    return parsed


def _read_usage(completion: ChatCompletion) -> TokenUsage | None:
    usage = completion.usage
    if usage is None:
        return None
    return TokenUsage(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )
