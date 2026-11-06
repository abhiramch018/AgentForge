"""Create an execution plan for one user goal and print it.

This calls the real LLM once when the first reply is valid (more only if
repair is needed). It does not execute any planned task.

Run from the project root:

    python -m examples.plan_request
"""

from app.core.config import load_settings
from app.llm import create_llm_client
from app.orchestration import Supervisor, execution_order


def main() -> None:
    settings = load_settings()
    llm_client = create_llm_client(settings)
    supervisor = Supervisor(llm_client)
    goal = (
        "Research competitor pricing for vendors A and B, then draft a "
        "one-page summary for leadership."
    )
    result = supervisor.create_plan(goal)

    print(f"goal={result.plan.goal}")
    if result.plan.summary:
        print(f"summary={result.plan.summary}")
    print(f"execution_order={execution_order(result.plan)}")
    for task in result.plan.tasks:
        deps = ",".join(task.depends_on) if task.depends_on else "-"
        print(
            f"- id={task.id} role={task.role.value} depends_on={deps}\n"
            f"  description={task.description}\n"
            f"  expected_output={task.expected_output}"
        )
    print(f"llm_calls={result.llm_calls}")
    print(f"duration_ms={result.duration_ms:.2f}")
    print(f"repaired={result.repaired}")
    print(f"model={result.model or 'unavailable'}")
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
