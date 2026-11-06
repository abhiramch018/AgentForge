"""M16 portfolio demo — offline, deterministic, no paid API calls.

Exercises: multi-task concurrent plan (via DemoLLM), review + one revision,
durable job, HITL artifact approval, memory write, second-run memory retrieval,
lifecycle events.

Windows::

    python -m examples.portfolio_demo_offline

Also suitable against Compose offline API (separate worker):: see DEPLOY.md /
docs/DEMO_SCRIPT.md for the dashboard path.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.api.concurrency import CapacityGate
from app.api.jobs.events import JobEventStore
from app.api.jobs.models import JobStatus
from app.api.jobs.queue import JobQueue
from app.api.jobs.runtime import JobRuntime, set_runtime
from app.api.jobs.store import JobStore
from app.api.service import OrchestrationService
from app.api.v1.schemas import RunRequest
from app.approval.models import ProtectedAction
from app.approval.service import ApprovalService
from app.approval.store import ApprovalStore
from app.llm.base import LLMClient
from app.llm.demo import DemoLLM, PORTFOLIO_DEMO_MARKER
from app.memory.embeddings import HashingEmbedder
from app.memory.service import MemoryService
from app.memory.store import MemoryStore

ROOT = Path(__file__).resolve().parents[1]
GOAL = (ROOT / "demo" / "portfolio_goal.md").read_text(encoding="utf-8")
ARTIFACT_BODY = (
    "# Vendor recommendation (portfolio demo)\n\n"
    "Proposed write after human approval.\n\n"
    "Winner: NordicCloud at $4,200/mo vs HarborStack at $5,100/mo (+$600 policy).\n"
    "Rationale: lower predictable cost + EU residency for this mid-size profile.\n"
    "Residual risk: no multi-region active-active.\n\n"
    "Labels: fake-model offline demo; supplied-evidence only; not live intelligence.\n"
)


def _build(root: Path) -> tuple[JobRuntime, ApprovalService, MemoryService, JobEventStore]:
    llm = DemoLLM()

    @contextmanager
    def scope() -> Iterator[LLMClient]:
        yield llm

    events = JobEventStore(root / "jobs.db", max_events_per_job=500)
    memory = MemoryService(
        MemoryStore(root / "memory.db"),
        workspace_id="portfolio-demo",
        embedder=HashingEmbedder(),
    )
    approvals = ApprovalService(
        ApprovalStore(root / "approvals.db"),
        workspace_id="portfolio-demo",
        artifact_root=str(root / "artifacts"),
        ttl_ms=3_600_000,
    )
    service = OrchestrationService(
        client_scope=scope,
        memory=memory,
        approval=approvals,
        events=events,
    )
    runtime = JobRuntime(
        store=JobStore(root / "jobs.db"),
        queue=JobQueue(root / "huey.db", immediate=True),
        service=service,
        capacity=CapacityGate(max_runs=4, max_tasks=16),
        max_run_concurrency=4,
        events=events,
        approval=approvals,
        worker_id="portfolio-demo",
    )
    set_runtime(runtime)
    return runtime, approvals, memory, events


def main() -> None:
    print("=== AgentForge M16 portfolio demo (OFFLINE / DemoLLM) ===")
    print(f"Marker: {PORTFOLIO_DEMO_MARKER}")
    print("Scripted replies are NOT evidence of real model intelligence.\n")

    with tempfile.TemporaryDirectory(prefix="agentforge-portfolio-") as tmp:
        root = Path(tmp)
        runtime, approvals, memory, events = _build(root)

        action = ProtectedAction(
            relative_path="portfolio/vendor_recommendation.md",
            content=ARTIFACT_BODY,
            reason="Persist the operator-approved vendor recommendation memo.",
        )
        req = RunRequest(
            goal=GOAL,
            review_enabled=True,
            max_revisions=1,
            memory_enabled=True,
            max_concurrency=4,
            protected_action=action,
        )

        print("1) Submit durable job (review + memory + protected_action)")
        job = runtime.submit(req)
        print(f"   job_id={job.job_id} status={job.status.value}")

        job = runtime.store.get(job.job_id)
        assert job.status is JobStatus.AWAITING_APPROVAL, job.status
        print("2) Job paused awaiting_approval (worker capacity released)")
        pending = approvals.list_pending()
        assert pending, "expected pending approval"
        appr = pending[0]
        print(f"   approval_id={appr.approval_id} path={appr.action.relative_path}")

        ev = events.list_since(job.job_id, after_event_id=0, limit=200)
        types = [e.event_type.value for e in ev]
        print(f"3) Lifecycle events captured: {len(ev)} (sample={types[:8]})")

        result = job.result or {}
        assert isinstance(result, dict)
        execution = result.get("execution") or {}
        task_results = execution.get("task_results") or []
        print(
            f"4) Partial checkpoint result: task_results={len(task_results)} "
            f"keys={sorted(result.keys())}"
        )
        # Expect concurrent analysts then writer (3 tasks in DemoLLM portfolio plan).
        if task_results:
            ids = [t.get("task_id") or t.get("id") for t in task_results if isinstance(t, dict)]
            print(f"   task_ids={ids}")

        print("5) Human approve exact fingerprint → resume write")
        approvals.approve(appr.approval_id, fingerprint=appr.fingerprint, note="demo approve")
        runtime.resume_after_approval(job.job_id, fingerprint=appr.fingerprint)
        job = runtime.store.get(job.job_id)
        assert job.status is JobStatus.SUCCEEDED, (job.status, job.error_message)
        artifact = root / "artifacts" / "portfolio" / "vendor_recommendation.md"
        assert artifact.is_file(), artifact
        print(f"   status=succeeded artifact={artifact}")

        print("6) Second job — memory reuse (HashingEmbedder, labeled lexical)")
        follow = runtime.submit(
            RunRequest(
                goal=(
                    f"{PORTFOLIO_DEMO_MARKER}\n"
                    "Using prior workspace memory if present, restate which vendor "
                    "was recommended and why in one short paragraph."
                ),
                review_enabled=False,
                memory_enabled=True,
                max_concurrency=2,
            )
        )
        follow = runtime.store.get(follow.job_id)
        print(f"   follow_job={follow.job_id} status={follow.status.value}")
        assert follow.status is JobStatus.SUCCEEDED

        mem_items = memory.list_memories(limit=20)
        print(f"7) Memory rows after runs: {len(mem_items)} (HashingEmbedder — not semantic prod retrieval)")
        print("\nDEMO_OK")


if __name__ == "__main__":
    main()
