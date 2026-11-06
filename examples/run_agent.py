"""Run one task through an agent and print the result.

Run from the project root:

    python -m examples.run_agent
"""

from app.agent import Agent, AgentRequest, AgentRunner
from app.core.config import load_settings
from app.llm import create_llm_client


def main() -> None:
    settings = load_settings()
    llm_client = create_llm_client(settings)
    agent = Agent(
        name="general_assistant",
        system_instruction="You are a helpful AI assistant.",
        llm_client=llm_client,
    )
    runner = AgentRunner()
    result = runner.run(
        agent,
        AgentRequest(task="Explain microservices in simple terms."),
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
