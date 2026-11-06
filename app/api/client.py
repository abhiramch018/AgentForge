"""Thin synchronous HTTP client for the orchestration service.

No automatic retries on ``POST /v1/runs``. A connect failure, read timeout,
or disconnect after the server may have started work is an **uncertain
outcome** — the client cannot know whether the run finished, and this
library will not silently re-issue the execute request.

``POST /v1/jobs`` is different: once HTTP 202 returns with a ``job_id``, the
client can poll status/result. Transport failure *before* 202 leaves
submission uncertain; do not assume the job exists without checking.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.api.jobs.models import (
    JobListResponse,
    JobResultResponse,
    JobStatus,
    JobStatusResponse,
    JobSubmitResponse,
)
from app.api.v1.schemas import (
    ErrorDetail,
    ErrorResponse,
    MemoryDeleteResponse,
    MemoryListResponse,
    MetaResponse,
    RunRequest,
    RunResponse,
)


class OrchestrationClientError(Exception):
    """Base client error."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class OrchestrationAPIError(OrchestrationClientError):
    """The service returned a structured error response."""

    def __init__(self, error: ErrorResponse, *, status_code: int) -> None:
        super().__init__(error.error.message, status_code=status_code)
        self.error = error


class OrchestrationTransportError(OrchestrationClientError):
    """Network failure or timeout with uncertain completion semantics."""


class OrchestrationClient:
    """Minimal sync client with explicit timeouts and no execute retries."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        connect_timeout_seconds: float = 2.0,
        read_timeout_seconds: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if connect_timeout_seconds <= 0 or read_timeout_seconds <= 0:
            raise ValueError("Timeouts must be greater than 0.")
        headers: dict[str, str] = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=read_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OrchestrationClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def health(self) -> dict[str, Any]:
        return self._request_json("GET", "/health")

    def meta(self) -> MetaResponse:
        payload = self._request_json("GET", "/v1/meta")
        return MetaResponse.model_validate(payload)

    def create_run(self, request: RunRequest) -> RunResponse:
        """Submit a run and wait for completion. Never retries automatically."""

        payload = self._request_json(
            "POST",
            "/v1/runs",
            json=request.model_dump(mode="json"),
        )
        return RunResponse.model_validate(payload)

    def submit_job(self, request: RunRequest) -> JobSubmitResponse:
        """Submit a background job. Expects HTTP 202. Never auto-retries."""

        payload = self._request_json(
            "POST",
            "/v1/jobs",
            json=request.model_dump(mode="json"),
            expected_status=202,
        )
        return JobSubmitResponse.model_validate(payload)

    def list_jobs(self, *, limit: int = 50, offset: int = 0) -> JobListResponse:
        payload = self._request_json(
            "GET",
            f"/v1/jobs?limit={limit}&offset={offset}",
        )
        return JobListResponse.model_validate(payload)

    def get_job(self, job_id: str) -> JobStatusResponse:
        payload = self._request_json("GET", f"/v1/jobs/{job_id}")
        return JobStatusResponse.model_validate(payload)

    def get_job_result(self, job_id: str) -> JobResultResponse:
        payload = self._request_json("GET", f"/v1/jobs/{job_id}/result")
        return JobResultResponse.model_validate(payload)

    def wait_for_job(
        self,
        job_id: str,
        *,
        poll_interval_seconds: float = 0.2,
        timeout_seconds: float = 60.0,
    ) -> JobStatusResponse:
        """Poll until terminal status or timeout. Does not re-submit."""

        deadline = time.monotonic() + timeout_seconds
        while True:
            status = self.get_job(job_id)
            if status.status in {JobStatus.SUCCEEDED, JobStatus.FAILED}:
                return status
            if time.monotonic() >= deadline:
                raise OrchestrationClientError(
                    f"Timed out waiting for job {job_id} "
                    f"(last status={status.status.value})."
                )
            time.sleep(poll_interval_seconds)

    def list_memory(
        self, *, limit: int = 50, offset: int = 0
    ) -> MemoryListResponse:
        payload = self._request_json(
            "GET",
            f"/v1/memory?limit={limit}&offset={offset}",
        )
        return MemoryListResponse.model_validate(payload)

    def delete_memory(self, memory_id: str) -> MemoryDeleteResponse:
        payload = self._request_json("DELETE", f"/v1/memory/{memory_id}")
        return MemoryDeleteResponse.model_validate(payload)

    def delete_all_memory(self) -> MemoryDeleteResponse:
        payload = self._request_json("DELETE", "/v1/memory")
        return MemoryDeleteResponse.model_validate(payload)

    def iter_job_events(
        self,
        job_id: str,
        *,
        last_event_id: int | None = None,
        timeout_seconds: float = 60.0,
    ):
        """Consume SSE lifecycle events via Authorization header (not URL).

        Yields parsed event dicts. This is not token streaming. Native
        browser ``EventSource`` cannot set Bearer headers — use fetch
        streaming or this client instead.
        """

        headers: dict[str, str] = {}
        if last_event_id is not None:
            headers["Last-Event-ID"] = str(last_event_id)
        deadline = time.monotonic() + timeout_seconds
        with self._client.stream(
            "GET",
            f"/v1/jobs/{job_id}/events",
            headers=headers,
            timeout=httpx.Timeout(timeout_seconds),
        ) as response:
            if response.status_code >= 400:
                error = _parse_error(response)
                raise OrchestrationAPIError(error, status_code=response.status_code)
            event_name = "message"
            event_id: str | None = None
            data_lines: list[str] = []
            for raw in response.iter_lines():
                if time.monotonic() >= deadline:
                    break
                if raw is None:
                    continue
                line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                if line.startswith(":"):
                    continue
                if not line:
                    if data_lines:
                        payload = "\n".join(data_lines)
                        data_lines = []
                        try:
                            data = json.loads(payload)
                        except ValueError:
                            data = {"raw": payload}
                        yield {
                            "event": event_name,
                            "id": event_id,
                            "data": data,
                        }
                        if isinstance(data, dict) and data.get("event_type") in {
                            "job.succeeded",
                            "job.failed",
                        }:
                            return
                    event_name = "message"
                    event_id = None
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("id:"):
                    event_id = line[3:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())


    def _request_json(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        *,
        expected_status: int | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, json=json)
        except httpx.TimeoutException as exc:
            raise OrchestrationTransportError(
                "The HTTP call timed out. If this was POST /v1/runs, the "
                "server may still be working; completion is uncertain and "
                "this client will not auto-retry. If this was POST /v1/jobs "
                "before a 202 response, submission is uncertain."
            ) from exc
        except httpx.HTTPError as exc:
            raise OrchestrationTransportError(
                "The HTTP call failed before a complete response was received. "
                "For POST /v1/runs, completion is uncertain and this client "
                "will not auto-retry. For POST /v1/jobs before 202, "
                "submission is uncertain."
            ) from exc

        if response.status_code >= 400:
            error = _parse_error(response)
            raise OrchestrationAPIError(error, status_code=response.status_code)
        if expected_status is not None and response.status_code != expected_status:
            raise OrchestrationClientError(
                f"Expected HTTP {expected_status}, got {response.status_code}.",
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise OrchestrationClientError(
                "The service returned a non-JSON response.",
                status_code=response.status_code,
            ) from exc
        if not isinstance(payload, dict):
            raise OrchestrationClientError(
                "The service returned JSON that was not an object.",
                status_code=response.status_code,
            )
        return payload


def _parse_error(response: httpx.Response) -> ErrorResponse:
    try:
        payload = response.json()
    except ValueError:
        return ErrorResponse(
            error=ErrorDetail(
                code="http_error",
                message=f"HTTP {response.status_code}",
            )
        )
    if isinstance(payload, dict) and "error" in payload:
        return ErrorResponse.model_validate(payload)
    if isinstance(payload, dict) and "detail" in payload:
        detail = payload["detail"]
        if isinstance(detail, dict) and "error" in detail:
            return ErrorResponse.model_validate(detail)
    return ErrorResponse(
        error=ErrorDetail(
            code="http_error",
            message=f"HTTP {response.status_code}",
        )
    )
