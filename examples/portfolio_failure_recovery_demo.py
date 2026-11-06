"""M16 failure/recovery demo — expired lease recovery (offline, no paid calls).

Simulates a worker crash while a job is ``running`` with an expired lease,
then shows ``recover()`` re-enqueueing and completing the job.

Windows::

    python -m examples.portfolio_failure_recovery_demo
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.api.concurrency import CapacityGate
from app.api.jobs.models import JobStatus
from app.api.jobs.queue import JobQueue
from app.api.jobs.runtime import JobRuntime, set_runtime
from app.api.jobs.store import JobStore
from app.api.service import OrchestrationService
from app.api.v1.schemas import PlanInput, RunRequest
from app.llm.base import LLMClient
from app.llm.demo import DemoLLM
from app.memory.service import NullMemoryService


def main() -> None:
    print("=== AgentForge M16 failure/recovery demo (OFFLINE) ===\n")
    with tempfile.TemporaryDirectory(prefix="agentforge-fail-") as tmp:
        root = Path(tmp)
        llm = DemoLLM()

        @contextmanager
        def scope() -> Iterator[LLMClient]:
            yield llm

        store = JobStore(root / "jobs.db")
        runtime = JobRuntime(
            store=store,
            queue=JobQueue(root / "huey.db", immediate=False),
            service=OrchestrationService(
                client_scope=scope, memory=NullMemoryService()
            ),
            capacity=CapacityGate(max_runs=2, max_tasks=8),
            max_run_concurrency=2,
            lease_ms=50.0,  # short lease for demo
            worker_id="recovery-demo",
        )
        set_runtime(runtime)

        job = runtime.submit(
            RunRequest(
                plan=PlanInput(
                    goal="Recovery demo task",
                    tasks=[
                        {
                            "id": "t1",
                            "description": "Produce a short AgentForge recovery note",
                            "role": "general",
                            "depends_on": [],
                            "expected_output": "One sentence",
                        }
                    ],
                ),
                memory_enabled=False,
                review_enabled=False,
            )
        )
        print(f"1) Submitted job_id={job.job_id} status={job.status.value}")

        # Simulate crash: claim as running with a lease that immediately expires.
        claimed = store.try_claim(
            job.job_id,
            owner_id="crashed-worker",
            lease_ms=1.0,
        )
        assert claimed is not None
        time.sleep(0.05)
        print("2) Simulated crash: job left running with expired lease")

        requeued = runtime.recover()
        print(f"3) recover() requeued={requeued}")
        assert job.job_id in requeued

        # Deliver with immediate queue for completion
        runtime.queue = JobQueue(root / "huey2.db", immediate=True)
        runtime.handle_delivery(job.job_id)
        final = store.get(job.job_id)
        print(f"4) After re-delivery status={final.status.value}")
        assert final.status is JobStatus.SUCCEEDED
        print("\nRECOVERY_OK — awaiting_approval jobs are NOT force-cancelled by recovery;")
        print("in-flight LLM HTTP calls are not forcibly aborted on SIGTERM.")


if __name__ == "__main__":
    main()
