#!/usr/bin/env python3
"""Hit a running Compose stack (offline profile) and verify persistence paths.

Expects web on http://127.0.0.1:8080 and ORCHESTRATION_API_TOKEN in the env
(or pass --token). Uses fake-LLM offline stack — no paid calls.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


def _req(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict | str]:
    data = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument(
        "--token",
        default=os.environ.get("ORCHESTRATION_API_TOKEN", "change-me-to-a-long-random-token"),
    )
    args = parser.parse_args()
    base = args.base.rstrip("/")
    token = args.token

    code, health = _req("GET", f"{base}/health")
    print("health", code, health)
    if code != 200:
        return 1

    code, ready = _req("GET", f"{base}/ready")
    print("ready", code, ready)
    if code != 200:
        return 1

    code, meta = _req("GET", f"{base}/v1/meta", token=token)
    print("meta", code, type(meta))
    if code != 200:
        return 1

    code, submit = _req(
        "POST",
        f"{base}/v1/jobs",
        token=token,
        body={
            "goal": "Compose verify: short AgentForge demo answer",
            "review_enabled": False,
            "memory_enabled": True,
        },
    )
    print("submit", code, submit)
    if code != 202 or not isinstance(submit, dict):
        return 1
    job_id = submit["job_id"]

    status = None
    for _ in range(60):
        code, status = _req("GET", f"{base}/v1/jobs/{job_id}", token=token)
        if code != 200 or not isinstance(status, dict):
            print("status_poll_fail", code, status)
            return 1
        st = status.get("status")
        print("status", st)
        if st in {"succeeded", "failed", "interrupted", "awaiting_approval"}:
            break
        time.sleep(0.5)
    else:
        print("timeout waiting for job")
        return 1

    code, result = _req("GET", f"{base}/v1/jobs/{job_id}/result", token=token)
    print("result", code, "keys" if isinstance(result, dict) else result)
    if status and status.get("status") == "succeeded" and code != 200:
        return 1

    code, jobs = _req("GET", f"{base}/v1/jobs?limit=5", token=token)
    print("list", code, isinstance(jobs, dict) and jobs.get("count"))
    print("VERIFY_OK", job_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
