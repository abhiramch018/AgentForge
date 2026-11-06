"""Offline M9 demo: run A stores context; run B retrieves it (fake LLM/embeddings).

Scripted retrieval proves wiring and trust labels — not real semantic quality.

Windows::

    python -m examples.memory_offline_demo
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from app.api.service import OrchestrationService
from app.api.v1.schemas import PlanInput, RunRequest
from app.core.messages import ChatMessage
from app.llm.base import LLMClient, LLMResponse
from app.memory.service import MemoryService
from app.memory.store import MemoryStore


class ScriptedLLM(LLMClient):
    def __init__(self, answers: Sequence[str]) -> None:
        self._answers = list(answers)
        self.prompts: list[str] = []

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        for msg in reversed(messages):
            if msg.role.value == "user":
                self.prompts.append(msg.content)
                break
        content = self._answers.pop(0) if self._answers else "done"
        return LLMResponse(content=content, model="fake-model")


def _plan(goal: str) -> PlanInput:
    return PlanInput(
        goal=goal,
        tasks=[
            {
                "id": "t1",
                "description": "Produce a short note",
                "role": "general",
                "depends_on": [],
                "expected_output": "One paragraph",
            }
        ],
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStore(Path(tmp) / "memory.db")
        memory = MemoryService(store, workspace_id="demo-workspace")

        llm_a = ScriptedLLM(
            [
                "Circuit breakers trip open after repeated failures to protect "
                "downstream services; prefer fail-fast over cascading timeouts."
            ]
        )

        @contextmanager
        def scope_a() -> Iterator[LLMClient]:
            yield llm_a

        print("Run A: store useful task summary (memory_enabled=true)")
        run_a = OrchestrationService(client_scope=scope_a, memory=memory).run(
            RunRequest(
                api_version="v1",
                plan=_plan("Document circuit-breaker guidance"),
                memory_enabled=True,
            ),
            run_id="run-a",
            concurrency=1,
        )
        print(
            f"  written={run_a.timing.memory.written_count if run_a.timing.memory else 0}"
        )
        print(f"  stored_items={len(memory.list_memories())}")

        llm_b = ScriptedLLM(["Reuse prior breaker note with caveats stated."])

        @contextmanager
        def scope_b() -> Iterator[LLMClient]:
            yield llm_b

        print("\nRun B: retrieve prior context (scripted — not semantic proof)")
        run_b = OrchestrationService(client_scope=scope_b, memory=memory).run(
            RunRequest(
                api_version="v1",
                plan=_plan("Refresh circuit-breaker guidance for on-call"),
                memory_enabled=True,
            ),
            run_id="run-b",
            concurrency=1,
        )
        metrics = run_b.timing.memory
        assert metrics is not None
        print(
            json.dumps(
                {
                    "returned_count": metrics.returned_count,
                    "context_chars": metrics.context_chars,
                    "retrieval_ms": metrics.retrieval_ms,
                    "embed_ms": metrics.embed_ms,
                    "embed_cache_hits": metrics.embed_cache_hits,
                },
                indent=2,
            )
        )
        print("\nSpecialist prompt excerpt (untrusted memory block present):")
        prompt = llm_b.prompts[0] if llm_b.prompts else ""
        for line in prompt.splitlines():
            if "memory" in line.lower() or "untrusted" in line.lower() or "<memory" in line:
                print(f"  {line}")
        print(
            "\nLabel: hashing embeddings + scripted LLM show wiring only; "
            "they do not prove real semantic retrieval quality."
        )


if __name__ == "__main__":
    main()
