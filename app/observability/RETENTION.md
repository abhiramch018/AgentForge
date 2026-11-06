# Telemetry retention and content capture (M12)

## Defaults (safe)

- Structured ops logs and OpenTelemetry spans **omit** credentials, Authorization
  headers, raw prompts, and generated content.
- Identifiers (`run_id`, `task_id`, `attempt`, W3C `traceparent`) stay in logs and
  spans — **not** as unbounded metric label values.
- Telemetry export/logging failures are swallowed; they must not fail jobs.

## Retention

| Store | Default intent | Mechanism |
| --- | --- | --- |
| Job lifecycle events (M11) | Cap per job | `ORCHESTRATION_EVENTS_MAX_PER_JOB` |
| Ops logs / OTel | Operator-controlled | Process stdout / optional console exporter; no built-in SaaS stack |
| Documented window | 14 days | `ORCHESTRATION_TELEMETRY_RETENTION_DAYS` (policy hint, not auto-purge) |

Operators should rotate or truncate log files and SQLite backups out of band.

## Opt-in content capture

Set `ORCHESTRATION_TELEMETRY_CAPTURE_CONTENT=true` only in tightly controlled
environments. Even then, API keys and Authorization headers remain redacted.

## Replay vs rerun

- `GET /v1/jobs/{id}/replay` → **timeline playback** from durable events.
- Re-executing work requires a **new** `POST /v1/jobs` and current approval rules;
  prior human approvals are never reused.
