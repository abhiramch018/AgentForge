"""Offline M10 demo: pause for approval, then resume artifact write (fake LLM).

Windows::

    python -m examples.approval_offline_demo
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from app.api.concurrency import CapacityGate
from app.api.jobs.queue import JobQueue
from app.api.jobs.runtime import JobRuntime, set_runtime
from app.api.jobs.store import JobStore
from app.api.service import OrchestrationService
from app.api.v1.schemas import PlanInput, RunRequest
from app.approval.models import ProtectedAction
from app.approval.service import ApprovalService
from app.approval.store import ApprovalStore
from app.core.messages import ChatMessage
from app.llm.base import LLMClient, LLMResponse
from app.memory.service import NullMemoryService


class ScriptedLLM(LLMClient):
    def __init__(self, answers: Sequence[str]) -> None:
        self._answers = list(answers)

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        content = self._answers.pop(0) if self._answers else "done"
        return LLMResponse(content=content, model="fake-model")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        llm = ScriptedLLM(["Draft release note body for operators."])

        @contextmanager
        def scope() -> Iterator[LLMClient]:
            yield llm

        approvals = ApprovalService(
            ApprovalStore(root / "approvals.db"),
            workspace_id="demo",
            artifact_root=str(root / "artifacts"),
        )
        service = OrchestrationService(
            client_scope=scope,
            memory=NullMemoryService(),
            approval=approvals,
        )
        runtime = JobRuntime(
            store=JobStore(root / "jobs.db"),
            queue=JobQueue(root / "huey.db", immediate=True),
            service=service,
            capacity=CapacityGate(max_runs=2, max_tasks=8),
            max_run_concurrency=4,
            approval=approvals,
            worker_id="demo-worker",
        )
        set_runtime(runtime)

        action = ProtectedAction(
            relative_path="notes/release.txt",
            content="Release 0.1.0 — HITL demo artifact.\n",
            reason="Write operator-facing release note under artifact root.",
        )
        request = RunRequest(
            api_version="v1",
            plan=PlanInput(
                goal="Prepare release note",
                tasks=[
                    {
                        "id": "draft",
                        "description": "Draft the note",
                        "role": "general",
                        "depends_on": [],
                        "expected_output": "Short draft",
                    }
                ],
            ),
            memory_enabled=False,
            protected_action=action,
        )

        print("1) Submit job with protected_action")
        job = runtime.submit(request)
        print(f"   status={job.status.value} (no worker slot held)")
        target = root / "artifacts" / "notes" / "release.txt"
        print(f"   artifact_exists_before_approval={target.exists()}")

        pending = approvals.list_pending()[0]
        print("2) Inspect proposal")
        print(f"   approval_id={pending.approval_id}")
        print(f"   fingerprint={pending.fingerprint[:16]}...")
        print(f"   risks={pending.risks[0]}")

        print("3) Approve (shared_operator) and resume")
        approvals.approve(pending.approval_id, fingerprint=pending.fingerprint)
        final = runtime.resume_after_approval(
            job.job_id, fingerprint=pending.fingerprint
        )
        print(f"   status={final.status.value}")
        print(f"   artifact_exists={target.exists()}")
        print(f"   artifact_preview={target.read_text(encoding='utf-8')!r}")
        print(
            "\nNote: M6 quality_approved is reviewer acceptance, not human "
            "authorization. Approval does not claim exactly-once side effects."
        )


if __name__ == "__main__":
    main()
