"""Call the local orchestration service over HTTP (live LLM).

Requires a running service and ORCHESTRATION_API_TOKEN. Spends API credits.

Terminal 1:
    uvicorn app.api.main:create_app --factory --port 8000

Terminal 2:
    python -m examples.run_via_api
"""

from app.api.client import OrchestrationClient
from app.api.v1.schemas import RunRequest
from app.core.config import load_settings
import os


def main() -> None:
    load_settings()  # ensure LLM env is present for the server process, not this client
    token = os.environ.get("ORCHESTRATION_API_TOKEN")
    if not token:
        raise SystemExit("Set ORCHESTRATION_API_TOKEN to match the server.")
    with OrchestrationClient(
        "http://127.0.0.1:8000",
        token=token,
        connect_timeout_seconds=2.0,
        read_timeout_seconds=180.0,
    ) as client:
        meta = client.meta()
        print(f"auth_required={meta.auth_required} tools_enabled={meta.tools_enabled}")
        result = client.create_run(
            RunRequest(
                goal="Explain microservices in one short paragraph.",
                review_enabled=False,
                max_concurrency=2,
            )
        )
    print(f"run_id={result.run_id}")
    print(f"status={result.status.value}")
    print(f"server_total_ms={result.timing.total_ms:.2f}")
    for task in result.execution.task_results:
        print(
            f"- {task.task_id}: status={task.status.value} "
            f"quality_approved={task.quality_approved} "
            f"policy={task.review_policy.value}"
        )
        if task.output:
            print(f"  output={task.output}")
    print(
        "note=If this client times out, completion is uncertain; "
        "the client does not auto-retry."
    )


if __name__ == "__main__":
    main()
