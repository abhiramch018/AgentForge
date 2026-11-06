"""Run one arithmetic task with the calculator tool.

Run from the project root:

    python -m examples.run_agent_with_tools
"""

import logging

from app.agent import Agent, AgentRequest, AgentRunner
from app.core.config import load_settings
from app.llm import create_llm_client
from app.tools import CalculatorTool, ToolRegistry


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = load_settings()
    llm_client = create_llm_client(settings)
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    agent = Agent(
        name="calculator_assistant",
        system_instruction=(
            "You are a helpful assistant. For arithmetic, call the calculator "
            "tool. Operations are add, subtract, multiply, and divide. After "
            "the tool returns, explain the result in one short sentence."
        ),
        llm_client=llm_client,
        allowed_tools=["calculator"],
    )
    runner = AgentRunner(registry)
    result = runner.run(
        agent,
        AgentRequest(task="Calculate 125 multiplied by 48 and explain the result."),
    )
    print(result.final_answer)
    print(f"agent={result.agent_name}")
    print(f"model={result.model}")
    if result.usage is None:
        print("usage=unavailable")
        return
    print(
        "usage="
        f"prompt_tokens={result.usage.prompt_tokens} "
        f"completion_tokens={result.usage.completion_tokens} "
        f"total_tokens={result.usage.total_tokens}"
    )


if __name__ == "__main__":
    main()
