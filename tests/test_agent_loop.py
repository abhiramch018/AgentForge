"""Unit tests for the bounded tool-calling loop.

The LLM client is scripted. These tests do not call the network.
"""

import ast
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from app.agent import (
    Agent,
    AgentMaxIterationsError,
    AgentRequest,
    AgentRunner,
)
from app.core.messages import ChatMessage, MessageRole, ToolCall
from app.llm.base import LLMClient, LLMResponse, TokenUsage
from app.tools import CalculatorTool, Tool, ToolRegistry, ToolResult, UnknownToolError


class ScriptedLLMClient(LLMClient):
    """Returns prepared responses and records the tools it was shown."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[list[ChatMessage]] = []
        self.tool_names: list[list[str]] = []

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Tool] | None = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        self.tool_names.append([tool.name for tool in tools or []])
        if not self._responses:
            raise AssertionError("The test client ran out of scripted responses.")
        return self._responses.pop(0)


class _NoteInput(BaseModel):
    text: str


class _FailingTool(Tool):
    name = "notes"
    description = "Store a note."
    input_model = _NoteInput

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(success=False, error="The note store is unavailable.")


class _RaisingTool(Tool):
    name = "notes"
    description = "Store a note."
    input_model = _NoteInput

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        raise RuntimeError("disk exploded at /secret/path")


class _SlowInput(BaseModel):
    pass


class _SlowTool(Tool):
    name = "slow"
    description = "Wait."
    input_model = _SlowInput

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        time.sleep(0.4)
        return ToolResult(success=True, output="done")


def test_runner_returns_a_final_response_without_tools() -> None:
    client = ScriptedLLMClient(
        [
            LLMResponse(
                content="Microservices are small services.",
                model="fake-model",
                usage=TokenUsage(prompt_tokens=4, completion_tokens=6, total_tokens=10),
            )
        ]
    )
    runner = AgentRunner(max_iterations=2)

    result = runner.run(_agent(client), AgentRequest(task="Explain microservices."))

    assert len(client.calls) == 1
    assert client.tool_names == [[]]
    assert result.final_answer == "Microservices are small services."
    assert result.agent_name == "research_assistant"
    assert result.model == "fake-model"
    assert result.usage is not None
    assert result.usage.total_tokens == 10


def test_runner_executes_one_tool_call_then_returns_the_final_answer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    client = ScriptedLLMClient(
        [
            _tool_response(
                ToolCall(
                    id="call_1",
                    name="calculator",
                    arguments={"operation": "multiply", "left": 125, "right": 48},
                )
            ),
            LLMResponse(content="125 multiplied by 48 is 6000.", model="fake-model"),
        ]
    )
    runner = AgentRunner(_calculator_registry(), max_iterations=4)

    result = runner.run(
        _agent(client, allowed_tools=["calculator"]),
        AgentRequest(task="Calculate 125 multiplied by 48."),
    )

    assert result.final_answer == "125 multiplied by 48 is 6000."
    assert len(client.calls) == 2
    tool_message = client.calls[1][-1]
    assert tool_message.role is MessageRole.TOOL
    assert tool_message.tool_call_id == "call_1"
    assert tool_message.content == "6000"
    assert "tool_requested" in caplog.text
    assert "tool_success" in caplog.text
    assert "final_response" in caplog.text
    assert "sk-" not in caplog.text


def test_runner_sends_a_tool_failure_back_to_the_model() -> None:
    client = ScriptedLLMClient(
        [
            _tool_response(
                ToolCall(
                    id="call_1",
                    name="notes",
                    arguments={"text": "hello"},
                )
            ),
            LLMResponse(content="I could not store the note.", model="fake-model"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_FailingTool())
    runner = AgentRunner(registry)

    result = runner.run(
        _agent(client, allowed_tools=["notes"]),
        AgentRequest(task="Store a note."),
    )

    assert client.calls[1][-1].content == "The note store is unavailable."
    assert result.final_answer == "I could not store the note."


def test_runner_hides_unexpected_tool_exceptions() -> None:
    client = ScriptedLLMClient(
        [
            _tool_response(
                ToolCall(id="call_1", name="notes", arguments={"text": "hello"})
            ),
            LLMResponse(content="The tool failed.", model="fake-model"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_RaisingTool())
    runner = AgentRunner(registry)

    result = runner.run(
        _agent(client, allowed_tools=["notes"]),
        AgentRequest(task="Store a note."),
    )

    assert client.calls[1][-1].content == "Tool 'notes' failed."
    assert "/secret/path" not in client.calls[1][-1].content
    assert result.final_answer == "The tool failed."


def test_runner_reports_an_unknown_tool_requested_by_the_model() -> None:
    client = ScriptedLLMClient(
        [
            _tool_response(
                ToolCall(id="call_1", name="web_search", arguments={"query": "news"})
            ),
            LLMResponse(content="I cannot search the web.", model="fake-model"),
        ]
    )
    runner = AgentRunner(_calculator_registry())

    result = runner.run(
        _agent(client, allowed_tools=["calculator"]),
        AgentRequest(task="Search the web."),
    )

    assert client.calls[1][-1].content == "Unknown tool 'web_search'."
    assert result.final_answer == "I cannot search the web."


def test_runner_rejects_malformed_tool_arguments() -> None:
    client = ScriptedLLMClient(
        [
            _tool_response(
                ToolCall(
                    id="call_1",
                    name="calculator",
                    arguments={"operation": "add", "left": "five", "right": 1},
                )
            ),
            LLMResponse(content="Those were not numbers.", model="fake-model"),
        ]
    )
    runner = AgentRunner(_calculator_registry())

    result = runner.run(
        _agent(client, allowed_tools=["calculator"]),
        AgentRequest(task="Add five and one."),
    )

    assert "Invalid arguments" in client.calls[1][-1].content
    assert result.final_answer == "Those were not numbers."


def test_runner_executes_multiple_tool_calls_in_order() -> None:
    client = ScriptedLLMClient(
        [
            LLMResponse(
                model="fake-model",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="calculator",
                        arguments={"operation": "add", "left": 2, "right": 3},
                    ),
                    ToolCall(
                        id="call_2",
                        name="calculator",
                        arguments={"operation": "subtract", "left": 9, "right": 4},
                    ),
                ],
            ),
            LLMResponse(content="The results are 5 and 5.", model="fake-model"),
        ]
    )
    runner = AgentRunner(_calculator_registry())

    result = runner.run(
        _agent(client, allowed_tools=["calculator"]),
        AgentRequest(task="Add 2 and 3, then subtract 4 from 9."),
    )

    tool_messages = [
        message
        for message in client.calls[1]
        if message.role is MessageRole.TOOL
    ]
    assert [message.tool_call_id for message in tool_messages] == ["call_1", "call_2"]
    assert [message.content for message in tool_messages] == ["5", "5"]
    assert result.final_answer == "The results are 5 and 5."


def test_runner_stops_at_the_maximum_iteration_limit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    call = ToolCall(
        id="call_1",
        name="calculator",
        arguments={"operation": "add", "left": 1, "right": 1},
    )
    client = ScriptedLLMClient([_tool_response(call), _tool_response(call)])
    runner = AgentRunner(_calculator_registry(), max_iterations=2)

    with pytest.raises(AgentMaxIterationsError, match="2 model calls"):
        runner.run(
            _agent(client, allowed_tools=["calculator"]),
            AgentRequest(task="Keep adding."),
        )

    assert len(client.calls) == 2
    assert "max_iterations_reached" in caplog.text


def test_runner_exposes_only_the_agents_allowed_tools() -> None:
    client = ScriptedLLMClient(
        [LLMResponse(content="No arithmetic was required.", model="fake-model")]
    )
    registry = _calculator_registry()
    registry.register(_FailingTool())
    runner = AgentRunner(registry)

    runner.run(
        _agent(client, allowed_tools=["calculator"]),
        AgentRequest(task="Say hello."),
    )

    assert client.tool_names == [["calculator"]]


def test_runner_rejects_an_allowed_tool_that_is_not_registered() -> None:
    client = ScriptedLLMClient([])

    with pytest.raises(UnknownToolError, match="not registered"):
        AgentRunner(ToolRegistry()).run(
            _agent(client, allowed_tools=["calculator"]),
            AgentRequest(task="Add 1 and 2."),
        )

    assert client.calls == []


def test_runner_reports_a_tool_timeout() -> None:
    client = ScriptedLLMClient(
        [
            _tool_response(ToolCall(id="call_1", name="slow", arguments={})),
            LLMResponse(content="The tool took too long.", model="fake-model"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_SlowTool())
    runner = AgentRunner(registry, tool_timeout_seconds=0.05)

    result = runner.run(
        _agent(client, allowed_tools=["slow"]),
        AgentRequest(task="Wait."),
    )

    assert client.calls[1][-1].content == "Tool 'slow' timed out."
    assert result.final_answer == "The tool took too long."


def test_runner_does_not_import_the_openai_sdk() -> None:
    source = Path("app/agent/runner.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        modules = _imported_modules(node)
        assert all(not module.startswith("openai") for module in modules)
        assert all(module != "app.llm.client" for module in modules)


def _agent(
    client: LLMClient,
    allowed_tools: list[str] | None = None,
) -> Agent:
    return Agent(
        name="research_assistant",
        system_instruction="You are a research assistant.",
        llm_client=client,
        allowed_tools=allowed_tools,
    )


def _calculator_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    return registry


def _tool_response(*calls: ToolCall) -> LLMResponse:
    return LLMResponse(model="fake-model", tool_calls=list(calls))


def _imported_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return [node.module or ""]
    return []
