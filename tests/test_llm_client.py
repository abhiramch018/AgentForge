"""Unit tests for configuration, messages, and the OpenAI client boundary.

These tests never call the network. Provider calls are replaced with a mock
of the official OpenAI SDK client.
"""

from unittest.mock import MagicMock, patch

import httpx2
import pytest
from openai import APIStatusError, APITimeoutError

# The current OpenAI SDK builds APITimeoutError from an httpx2.Request.
from pydantic import SecretStr, ValidationError

from app.core.config import Settings, load_settings
from app.core.exceptions import (
    LLMConfigurationError,
    LLMProviderError,
    LLMTimeoutError,
)
from app.core.messages import ChatMessage, MessageRole, ToolCall
from app.llm.client import OpenAIChatClient
from app.tools import CalculatorTool

API_KEY = "sk-test-key"


def test_load_settings_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "OpenAI")
    monkeypatch.setenv("LLM_MODEL", " gpt-4o-mini ")
    monkeypatch.setenv("LLM_API_KEY", f" {API_KEY} ")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "128")

    settings = load_settings(env_file=None)

    assert settings.llm_provider == "openai"
    assert settings.llm_model == "gpt-4o-mini"
    assert settings.llm_api_key.get_secret_value() == API_KEY
    assert settings.llm_timeout_seconds == 12.5
    assert settings.llm_max_output_tokens == 128
    assert API_KEY not in repr(settings)


def test_load_settings_reports_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")

    with pytest.raises(LLMConfigurationError, match="llm_api_key"):
        load_settings(env_file=None)


def test_load_settings_rejects_blank_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_llm_env(monkeypatch)
    _set_required_env(monkeypatch, api_key="   ")

    with pytest.raises(LLMConfigurationError, match="llm_api_key"):
        load_settings(env_file=None)


def test_load_settings_rejects_unknown_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_llm_env(monkeypatch)
    _set_required_env(
        monkeypatch,
        provider="anthropic",
        api_key="sk-super-secret-value",
    )

    with pytest.raises(LLMConfigurationError, match="llm_provider") as caught:
        load_settings(env_file=None)

    assert "sk-super-secret-value" not in str(caught.value)


def test_load_settings_rejects_non_positive_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_llm_env(monkeypatch)
    _set_required_env(monkeypatch)
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "0")

    with pytest.raises(LLMConfigurationError, match="llm_timeout_seconds"):
        load_settings(env_file=None)


def test_chat_message_accepts_each_role() -> None:
    messages = [
        ChatMessage(role=MessageRole.SYSTEM, content="You are concise."),
        ChatMessage(role=MessageRole.USER, content="Hello"),
        ChatMessage(role=MessageRole.ASSISTANT, content="Hi"),
        ChatMessage(
            role=MessageRole.TOOL,
            content="tool output",
            tool_call_id="call-1",
            tool_name="calculator",
        ),
    ]

    assert [message.role.value for message in messages] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]


def test_chat_message_rejects_unknown_role_and_blank_content() -> None:
    with pytest.raises(ValidationError):
        ChatMessage(role="moderator", content="Hello")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        ChatMessage(role=MessageRole.USER, content="   ")


def test_generate_returns_normalized_response() -> None:
    settings = _settings()
    sdk = MagicMock()
    sdk.chat.completions.create.return_value = _completion(
        content="An LLM client sends messages and returns a normalized reply.",
        model="gpt-4o-mini-2026-01-01",
        prompt_tokens=18,
        completion_tokens=11,
        total_tokens=29,
    )
    messages = [
        ChatMessage(role=MessageRole.SYSTEM, content="Be brief."),
        ChatMessage(role=MessageRole.USER, content="What is an LLM client?"),
    ]

    with patch("app.llm.client.OpenAI", return_value=sdk) as openai_cls:
        client = OpenAIChatClient(settings)
        response = client.generate(messages)

    openai_cls.assert_called_once_with(
        api_key=API_KEY,
        timeout=12.5,
        max_retries=0,
    )
    sdk.chat.completions.create.assert_called_once_with(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "What is an LLM client?"},
        ],
        max_completion_tokens=128,
    )
    assert response.content.startswith("An LLM client")
    assert response.model == "gpt-4o-mini-2026-01-01"
    assert response.usage is not None
    assert response.usage.prompt_tokens == 18
    assert response.usage.completion_tokens == 11
    assert response.usage.total_tokens == 29


def test_generate_allows_missing_usage() -> None:
    settings = _settings()
    sdk = MagicMock()
    completion = _completion(content="Hello", model="gpt-4o-mini")
    completion.usage = None
    sdk.chat.completions.create.return_value = completion

    with patch("app.llm.client.OpenAI", return_value=sdk):
        response = OpenAIChatClient(settings).generate(
            [ChatMessage(role=MessageRole.USER, content="Hello")]
        )

    assert response.content == "Hello"
    assert response.usage is None


def test_generate_translates_provider_errors() -> None:
    settings = _settings()
    sdk = MagicMock()
    request = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx2.Response(status_code=500, request=request)
    sdk.chat.completions.create.side_effect = APIStatusError(
        "server error",
        response=response,
        body=None,
    )

    with patch("app.llm.client.OpenAI", return_value=sdk):
        client = OpenAIChatClient(settings)
        with pytest.raises(LLMProviderError, match="provider request failed"):
            client.generate([ChatMessage(role=MessageRole.USER, content="Hello")])


def test_generate_translates_timeouts() -> None:
    settings = _settings()
    sdk = MagicMock()
    request = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")
    sdk.chat.completions.create.side_effect = APITimeoutError(request)

    with patch("app.llm.client.OpenAI", return_value=sdk):
        client = OpenAIChatClient(settings)
        with pytest.raises(LLMTimeoutError, match="timed out"):
            client.generate([ChatMessage(role=MessageRole.USER, content="Hello")])


def test_generate_rejects_empty_message_list_without_calling_provider() -> None:
    settings = _settings()
    sdk = MagicMock()

    with patch("app.llm.client.OpenAI", return_value=sdk):
        client = OpenAIChatClient(settings)
        with pytest.raises(LLMProviderError, match="At least one message"):
            client.generate([])

    sdk.chat.completions.create.assert_not_called()


def test_generate_advertises_tools_and_normalizes_tool_calls() -> None:
    settings = _settings()
    sdk = MagicMock()
    sdk.chat.completions.create.return_value = _tool_call_completion()
    messages = [
        ChatMessage(role=MessageRole.USER, content="What is 125 times 48?"),
        ChatMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="calculator",
                    arguments={"operation": "multiply", "left": 125, "right": 48},
                )
            ],
        ),
        ChatMessage(
            role=MessageRole.TOOL,
            content="6000",
            tool_call_id="call_1",
            tool_name="calculator",
        ),
    ]

    with patch("app.llm.client.OpenAI", return_value=sdk):
        response = OpenAIChatClient(settings).generate(messages, [CalculatorTool()])

    request = sdk.chat.completions.create.call_args.kwargs
    assert request["tools"][0]["function"]["name"] == "calculator"
    assert "operation" in request["tools"][0]["function"]["parameters"]["properties"]
    assert request["messages"][1]["tool_calls"][0]["function"]["name"] == "calculator"
    assert request["messages"][2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "6000",
    }
    assert response.content is None
    assert response.tool_calls[0].id == "call_1"
    assert response.tool_calls[0].name == "calculator"
    assert response.tool_calls[0].arguments == {
        "operation": "multiply",
        "left": 2,
        "right": 3,
    }


def test_generate_rejects_a_response_with_no_text() -> None:
    settings = _settings()
    sdk = MagicMock()
    completion = _completion(content="unused", model="gpt-4o-mini")
    completion.choices[0].message.content = None
    sdk.chat.completions.create.return_value = completion

    with patch("app.llm.client.OpenAI", return_value=sdk):
        client = OpenAIChatClient(settings)
        with pytest.raises(LLMProviderError, match="no text content"):
            client.generate([ChatMessage(role=MessageRole.USER, content="Hello")])


def _settings() -> Settings:
    return Settings(
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        llm_api_key=SecretStr(API_KEY),
        llm_timeout_seconds=12.5,
        llm_max_output_tokens=128,
        _env_file=None,
    )


def _completion(
    *,
    content: str,
    model: str,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
) -> MagicMock:
    completion = MagicMock()
    completion.model = model
    completion.choices = [MagicMock()]
    completion.choices[0].message.content = content
    if prompt_tokens is None:
        completion.usage = None
    else:
        completion.usage.prompt_tokens = prompt_tokens
        completion.usage.completion_tokens = completion_tokens
        completion.usage.total_tokens = total_tokens
    return completion


def _tool_call_completion() -> MagicMock:
    call = MagicMock()
    call.id = "call_1"
    call.function.name = "calculator"
    call.function.arguments = '{"operation": "multiply", "left": 2, "right": 3}'
    message = MagicMock()
    message.content = None
    message.tool_calls = [call]
    completion = MagicMock()
    completion.model = "gpt-4o-mini"
    completion.choices = [MagicMock()]
    completion.choices[0].message = message
    completion.usage = None
    return completion


def _clear_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_API_KEY",
        "LLM_TIMEOUT_SECONDS",
        "LLM_MAX_OUTPUT_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)


def _set_required_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: str = "openai",
    api_key: str = API_KEY,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("LLM_API_KEY", api_key)
