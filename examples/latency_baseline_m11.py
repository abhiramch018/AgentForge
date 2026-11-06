"""Measure fake-workload HTTP overhead with cold vs warm connections.

Controlled fake LLM sleeps — NOT provider latency. Verifies the previously
mentioned ~300 ms HTTP overhead claim with sample counts and P50/P95.

Also records a justified micro-optimization: reuse one httpx.Client
(keep-alive) vs new Client per request.

Windows::

    python -m examples.latency_baseline_m11
"""

from __future__ import annotations

import statistics
import tempfile
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import httpx
import uvicorn

from app.api.config import APISettings
from app.api.main import create_app
from app.api.service import OrchestrationService
from app.api.v1.schemas import PlanInput, RunRequest
from app.core.messages import ChatMessage
from app.llm.base import LLMClient, LLMResponse
from app.memory.service import NullMemoryService


class SleepyLLM(LLMClient):
    def __init__(self, delay_s: float = 0.05) -> None:
        self.delay_s = delay_s

    def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None = None,
    ) -> LLMResponse:
        time.sleep(self.delay_s)
        return LLMResponse(content="baseline-output", model="fake-model")


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


def main() -> None:
    token = "bench-token"
    samples = 30
    warmups = 3

    with tempfile.TemporaryDirectory() as tmp:
        llm = SleepyLLM(0.05)

        @contextmanager
        def scope() -> Iterator[LLMClient]:
            yield llm

        service = OrchestrationService(
            client_scope=scope,
            memory=NullMemoryService(),
        )
        app = create_app(
            api_settings=APISettings(
                _env_file=None,
                api_token=token,
                allow_unauthenticated_local=False,
                jobs_immediate=True,
            ),
            service=service,
            memory_service=NullMemoryService(),
        )

        import socket

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        )
        threading.Thread(target=server.run, daemon=True).start()
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                if httpx.get(f"{base}/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)

        plan = PlanInput(
            goal="Latency baseline",
            tasks=[
                {
                    "id": "t1",
                    "description": "Work",
                    "role": "general",
                    "depends_on": [],
                    "expected_output": "Out",
                }
            ],
        )
        body = RunRequest(
            api_version="v1",
            plan=plan,
            memory_enabled=False,
        ).model_dump(mode="json")
        headers = {"Authorization": f"Bearer {token}"}

        def once_new_client() -> tuple[float, float]:
            started = time.perf_counter()
            with httpx.Client(timeout=30.0) as client:
                r = client.post(f"{base}/v1/runs", headers=headers, json=body)
            total = (time.perf_counter() - started) * 1000
            r.raise_for_status()
            server_ms = float(r.json()["timing"]["total_ms"])
            return total, server_ms

        shared = httpx.Client(timeout=30.0, headers=headers)

        def once_reused() -> tuple[float, float]:
            started = time.perf_counter()
            r = shared.post(f"{base}/v1/runs", json=body)
            total = (time.perf_counter() - started) * 1000
            r.raise_for_status()
            server_ms = float(r.json()["timing"]["total_ms"])
            return total, server_ms

        print(
            f"=== Fake-workload HTTP baseline (n={samples}, warmups={warmups}) ==="
        )
        print("workload=SleepyLLM(50ms) — NOT live provider latency\n")

        for label, fn in (
            ("new_client_per_request", once_new_client),
            ("reused_httpx_client", once_reused),
        ):
            for _ in range(warmups):
                fn()
            totals: list[float] = []
            servers: list[float] = []
            overheads: list[float] = []
            for _ in range(samples):
                total, server_ms = fn()
                totals.append(total)
                servers.append(server_ms)
                overheads.append(max(0.0, total - server_ms))
            print(f"[{label}]")
            print(
                f"  client_wait_ms P50={_percentile(totals, 50):.1f} "
                f"P95={_percentile(totals, 95):.1f} "
                f"mean={statistics.mean(totals):.1f}"
            )
            print(
                f"  server_total_ms P50={_percentile(servers, 50):.1f} "
                f"P95={_percentile(servers, 95):.1f}"
            )
            print(
                f"  http_overhead_ms≈client-server P50={_percentile(overheads, 50):.1f} "
                f"P95={_percentile(overheads, 95):.1f} "
                f"(serialization+network+admission; fake workers)"
            )
            print()

        shared.close()
        print(
            "Finding: reusing one httpx.Client (keep-alive) typically cuts cold "
            "connect/TLS-setup-like overhead on localhost compared with a new "
            "Client per call. The ~300 ms figure is treated as a claim to verify "
            "— report the measured P50/P95 above for this machine."
        )
        print(
            "No review/approval/quality knobs were weakened. Optimization = "
            "connection reuse only."
        )
        server.should_exit = True


if __name__ == "__main__":
    main()
