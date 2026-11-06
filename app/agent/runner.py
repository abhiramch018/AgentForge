"""Execute an agent request, including a bounded tool-calling loop.

The runner decides when a tool call runs. The LLM client only talks to the
model. Tool calls in one response run one after another; parallel execution
is intentionally not implemented.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from pydantic import ValidationError

from app.agent.agent import Agent
from app.agent.exceptions import (
    AgentConfigurationError,
    AgentMaxIterationsError,
    format_validation_error,
)
from app.agent.models import AgentRequest, AgentResult
from app.core.messages import ChatMessage, MessageRole, ToolCall
from app.llm.base import TokenUsage
from app.tools.base import Tool, ToolResult, UnknownToolError
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 4
DEFAULT_TOOL_TIMEOUT_SECONDS = 5.0


class AgentRunner:
    """Executes an agent request."""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        *,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        tool_timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
    ) -> None:
        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int):
            raise AgentConfigurationError("max_iterations must be a positive integer.")
        if max_iterations < 1:
            raise AgentConfigurationError("max_iterations must be a positive integer.")
        if isinstance(tool_timeout_seconds, bool) or not isinstance(
            tool_timeout_seconds,
            (int, float),
        ):
            raise AgentConfigurationError("tool_timeout_seconds must be greater than 0.")
        if tool_timeout_seconds <= 0:
            raise AgentConfigurationError("tool_timeout_seconds must be greater than 0.")
        self._registry = registry if registry is not None else ToolRegistry()
        self.max_iterations = max_iterations
        self.tool_timeout_seconds = float(tool_timeout_seconds)

    def run(self, agent: Agent, request: AgentRequest) -> AgentResult:
        tools = self._allowed_tools(agent)
        messages = agent.build_messages(request.task)
        usage: TokenUsage | None = None
        logger.info(
            "agent_run_start agent=%s allowed_tools=%s max_iterations=%s",
            agent.name,
            [tool.name for tool in tools],
            self.max_iterations,
        )
        for iteration in range(1, self.max_iterations + 1):
            logger.info("llm_call agent=%s iteration=%s", agent.name, iteration)
            response = agent.llm_client.generate(messages, tools)
            usage = _combine_usage(usage, response.usage)
            if response.tool_calls:
                messages.append(
                    ChatMessage(
                        role=MessageRole.ASSISTANT,
                        content=response.content or "",
                        tool_calls=list(response.tool_calls),
                    )
                )
                for call in response.tool_calls:
                    messages.append(self._run_tool_call(agent, iteration, call, tools))
                continue
            final_answer = response.content or ""
            logger.info(
                "final_response agent=%s iteration=%s model=%s",
                agent.name,
                iteration,
                response.model,
            )
            return AgentResult(
                final_answer=final_answer,
                agent_name=agent.name,
                model=response.model,
                usage=usage,
            )
        logger.warning(
            "max_iterations_reached agent=%s max_iterations=%s",
            agent.name,
            self.max_iterations,
        )
        raise AgentMaxIterationsError(
            f"Agent '{agent.name}' stopped after {self.max_iterations} model "
            "calls without a final answer."
        )

    def _allowed_tools(self, agent: Agent) -> list[Tool]:
        allowed: list[Tool] = []
        for name in agent.allowed_tools:
            try:
                allowed.append(self._registry.get(name))
            except UnknownToolError as exc:
                raise UnknownToolError(
                    f"Agent '{agent.name}' allows '{name}', which is not registered."
                ) from exc
        return allowed

    def _run_tool_call(
        self,
        agent: Agent,
        iteration: int,
        call: ToolCall,
        tools: list[Tool],
    ) -> ChatMessage:
        logger.info(
            "tool_requested agent=%s iteration=%s tool=%s call_id=%s arguments=%s",
            agent.name,
            iteration,
            call.name,
            call.id,
            call.arguments,
        )
        result = self._execute(call, tools)
        if result.success:
            logger.info(
                "tool_success agent=%s tool=%s call_id=%s",
                agent.name,
                call.name,
                call.id,
            )
        else:
            logger.info(
                "tool_failure agent=%s tool=%s call_id=%s error=%s",
                agent.name,
                call.name,
                call.id,
                result.error,
            )
        return _tool_message(call, result)

    def _execute(self, call: ToolCall, tools: list[Tool]) -> ToolResult:
        tool = _find_tool(call.name, tools)
        if tool is None:
            return ToolResult(
                success=False,
                error=f"Unknown tool '{call.name}'.",
            )
        try:
            tool.input_model.model_validate(call.arguments)
        except ValidationError as exc:
            return ToolResult(
                success=False,
                error=(
                    f"Invalid arguments for '{call.name}'. "
                    f"{format_validation_error(exc)}"
                ),
            )
        logger.info("tool_execution tool=%s call_id=%s", tool.name, call.id)
        try:
            return _execute_with_timeout(
                tool,
                dict(call.arguments),
                self.tool_timeout_seconds,
            )
        except TimeoutError:
            return ToolResult(
                success=False,
                error=f"Tool '{tool.name}' timed out.",
            )
        except Exception:
            return ToolResult(success=False, error=f"Tool '{tool.name}' failed.")


def _find_tool(name: str, tools: list[Tool]) -> Tool | None:
    for tool in tools:
        if tool.name == name:
            return tool
    return None


def _execute_with_timeout(
    tool: Tool,
    arguments: dict[str, object],
    timeout_seconds: float,
) -> ToolResult:
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(tool.execute, arguments)
    try:
        return future.result(timeout=timeout_seconds)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _tool_message(call: ToolCall, result: ToolResult) -> ChatMessage:
    if result.success:
        content = result.output or ""
    else:
        content = result.error or "Tool failed."
    return ChatMessage(
        role=MessageRole.TOOL,
        content=content,
        tool_call_id=call.id,
        tool_name=call.name,
    )


def _combine_usage(current: TokenUsage | None, added: TokenUsage | None) -> TokenUsage | None:
    if added is None:
        return current
    if current is None:
        return added.model_copy()
    return TokenUsage(
        prompt_tokens=_sum_counts(current.prompt_tokens, added.prompt_tokens),
        completion_tokens=_sum_counts(
            current.completion_tokens,
            added.completion_tokens,
        ),
        total_tokens=_sum_counts(current.total_tokens, added.total_tokens),
    )


def _sum_counts(left: int | None, right: int | None) -> int | None:
    if left is None or right is None:
        return None
    return left + right
