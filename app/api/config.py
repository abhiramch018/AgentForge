"""Service settings, separate from provider credentials.

Capacity limits apply to **one OS process** (one service or worker instance).
They do not coordinate across replicas.

Memory is scoped to ``workspace_id`` from this trusted configuration — not
from arbitrary request fields. With a shared bearer token the deployment is
a single workspace, not multi-user isolation.
"""

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class APISettings(BaseSettings):
    """HTTP orchestration service configuration."""

    model_config = SettingsConfigDict(
        env_prefix="ORCHESTRATION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_token: SecretStr | None = None
    allow_unauthenticated_local: bool = False
    max_inflight_runs: int = Field(default=2, ge=1, le=16)
    max_inflight_tasks: int = Field(default=8, ge=1, le=32)
    max_run_concurrency: int = Field(default=4, ge=1, le=32)

    # M8: durable jobs (SQLite store) + Huey SqliteHuey delivery
    jobs_db_path: str = "data/jobs.db"
    queue_db_path: str = "data/huey_queue.db"
    max_job_attempts: int = Field(default=2, ge=1, le=8)
    job_lease_ms: float = Field(default=120_000.0, gt=1_000.0)
    jobs_immediate: bool = False  # True only for in-process tests

    # M11: durable lifecycle events (default: same SQLite file as jobs)
    events_db_path: str | None = None  # None → jobs_db_path
    events_max_per_job: int = Field(default=500, ge=50, le=5000)
    events_poll_interval_ms: float = Field(default=100.0, ge=20.0, le=5000.0)
    events_heartbeat_seconds: float = Field(default=15.0, ge=1.0, le=120.0)

    # M9: durable memory (SQLite + in-process cosine over stored vectors)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    memory_db_path: str = "data/memory.db"
    memory_enabled_default: bool = True
    memory_retention_days: float = Field(default=30.0, ge=0.0, le=3650.0)
    memory_top_k: int = Field(default=5, ge=1, le=20)
    memory_max_context_chars: int = Field(default=2500, ge=200, le=20000)

    # M10: human-in-the-loop approval (shared token = shared-operator, not identity)
    approvals_db_path: str = "data/approvals.db"
    artifact_output_root: str = "data/artifacts"
    approval_ttl_ms: float = Field(default=3_600_000.0, gt=1_000.0)

    # M12: observability (light OTel; no mandatory external stack)
    otel_console: bool = False
    # Opt-in: include prompt/content keys in ops logs / span attrs (still never
    # logs Authorization headers or API keys). Default false.
    telemetry_capture_content: bool = False
    # Documented retention intent for operators (not an automatic purge cron).
    telemetry_retention_days: float = Field(default=14.0, ge=0.0, le=3650.0)
    pricing_table_path: str | None = None  # None → packaged fixture

    @model_validator(mode="after")
    def require_auth_configuration(self) -> "APISettings":
        if self.api_token is not None and not self.api_token.get_secret_value().strip():
            raise ValueError("ORCHESTRATION_API_TOKEN must not be blank when set.")
        if self.api_token is None and not self.allow_unauthenticated_local:
            raise ValueError(
                "Set ORCHESTRATION_API_TOKEN, or explicitly set "
                "ORCHESTRATION_ALLOW_UNAUTHENTICATED_LOCAL=true for local development."
            )
        if self.max_inflight_tasks < self.max_run_concurrency:
            raise ValueError(
                "max_inflight_tasks must be >= max_run_concurrency so one run "
                "can reserve its worker slots."
            )
        cleaned = self.workspace_id.strip()
        if not cleaned:
            raise ValueError("ORCHESTRATION_WORKSPACE_ID must not be blank.")
        self.workspace_id = cleaned
        return self

    @property
    def auth_required(self) -> bool:
        return self.api_token is not None


def load_api_settings(*, env_file: str | None = ".env") -> APISettings:
    if env_file is None:
        return APISettings(_env_file=None)
    return APISettings(_env_file=env_file)
