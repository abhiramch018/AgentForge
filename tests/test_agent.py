"""Unit tests for the single-step agent runtime.

The LLM client is a fake. These tests do not call the network.
"""

import ast
from collections.abc import Sequence
from pathlib import Path

import pytest

from app.agent import (
    Agent,
    AgentConfigurationError,
    AgentRequest,
    AgentRequestError,
    AgentRunner,
)
from app.core.exceptions import LLMProviderError, LLMTimeoutError
from app.core.messages import ChatMessage, MessageRole
from app.llm.base import LLMClient, LLMResponse, TokenUsage


class FakeLLMClient(LLMClient):
    """Records each generate() call and returns a prepared response."""

    def __init__(self, response: LLMResponse | None = None) -> None:
        self.response = response or LLMResponse(
            content="Microservices are small independent services.",
            model="fake-model",
            usage=TokenUsage(
                prompt_tokens=12,
                completion_tokens=8,
                total_tokens=20,
            ),
        )
        self.calls: list[list[ChatMessage]] = []
        self.tools_seen: list[list[object]] = []
        self.error: Exception | None = None

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        self.tools_seen.append(list(tools or []))
        if self.error is not None:
            raise self.error
        return self.response


def test_agent_creation() -> None:
    client = FakeLLMClient()
    agent = Agent(
        name="  general_assistant  ",
        system_instruction="  You are a helpful AI assistant.  ",
        llm_client=client,
    )

    assert agent.name == "general_assistant"
    assert agent.system_instruction == "You are a helpful AI assistant."
    assert agent.llm_client is client


def test_agent_rejects_blank_name_and_instruction() -> None:
    client = FakeLLMClient()

    with pytest.raises(AgentConfigurationError, match="name must not be blank"):
        Agent(name="   ", system_instruction="Be helpful.", llm_client=client)

    with pytest.raises(
        AgentConfigurationError,
        match="system_instruction must not be blank",
    ):
        Agent(name="helper", system_instruction="   ", llm_client=client)


def test_agent_rejects_an_object_that_is_not_an_llm_client() -> None:
    with pytest.raises(AgentConfigurationError, match="llm_client"):
        Agent(
            name="helper",
            system_instruction="Be helpful.",
            llm_client=object(),  # type: ignore[arg-type]
        )


def test_empty_task_is_rejected_before_the_model_is_called() -> None:
    client = FakeLLMClient()
    agent = _agent(client)

    with pytest.raises(AgentRequestError, match="task"):
        agent.run("   ")

    with pytest.raises(AgentRequestError, match="task"):
        AgentRequest(task="")

    assert client.calls == []


def test_build_messages_starts_with_the_system_instruction() -> None:
    agent = _agent(FakeLLMClient())
    messages = agent.build_messages("Explain microservices.")

    assert messages[0] == ChatMessage(
        role=MessageRole.SYSTEM,
        content="You are a research assistant.",
    )


def test_build_messages_puts_the_task_in_the_user_message() -> None:
    agent = _agent(FakeLLMClient())
    messages = agent.build_messages("Explain microservices.")

    assert messages[1] == ChatMessage(
        role=MessageRole.USER,
        content="Explain microservices.",
    )
    assert len(messages) == 2


def test_run_calls_the_llm_client_once_and_returns_an_agent_result() -> None:
    client = FakeLLMClient()
    agent = _agent(client)

    result = agent.run("Explain microservices.")

    assert len(client.calls) == 1
    assert client.calls[0][0].role is MessageRole.SYSTEM
    assert client.calls[0][1].content == "Explain microservices."
    assert result.final_answer == "Microservices are small independent services."
    assert result.agent_name == "research_assistant"
    assert result.model == "fake-model"
    assert result.usage is not None
    assert result.usage.total_tokens == 20


def test_run_propagates_llm_client_failures() -> None:
    provider_error = LLMProviderError("The LLM provider request failed.")
    timeout_error = LLMTimeoutError("The LLM request timed out.")
    provider_client = FakeLLMClient()
    provider_client.error = provider_error
    timeout_client = FakeLLMClient()
    timeout_client.error = timeout_error

    with pytest.raises(LLMProviderError) as provider_caught:
        _agent(provider_client).run("Explain microservices.")
    with pytest.raises(LLMTimeoutError) as timeout_caught:
        _agent(timeout_client).run("Explain microservices.")

    assert provider_caught.value is provider_error
    assert timeout_caught.value is timeout_error


def test_runner_executes_one_agent_request() -> None:
    client = FakeLLMClient()
    agent = _agent(client)
    runner = AgentRunner()

    result = runner.run(agent, AgentRequest(task="Explain microservices."))

    assert len(client.calls) == 1
    assert result.final_answer == client.response.content
    assert result.agent_name == agent.name


def test_agent_package_does_not_import_the_openai_sdk() -> None:
    package = Path("app/agent")
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = _imported_modules(node)
            assert all(not module.startswith("openai") for module in modules)
            assert all(module != "app.llm.client" for module in modules)
            assert all(module != "app.llm" for module in modules)


def _agent(client: LLMClient) -> Agent:
    return Agent(
        name="research_assistant",
        system_instruction="You are a research assistant.",
        llm_client=client,
    )


def _imported_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return [node.module or ""]
    return []
