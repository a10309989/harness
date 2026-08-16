"""Environment-backed runtime configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
    """Configuration shared by the API, workers, and infrastructure adapters."""

    model_config = SettingsConfigDict(
        env_prefix="HARNESS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    database_url: str = "postgresql://harness:harness-local-dev@127.0.0.1:5432/harness"
    artifact_storage_path: Path = Path("data/artifacts")
    artifact_storage_backend: str = "local"
    config_dir: Path = Path("config")
    security_mode: str = "dev"
    cors_allowed_origins: str = "*"
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = 600
    rate_limit_auth_failures_per_minute: int = 10
    rate_limit_auth_failure_window_seconds: int = 60
    gateway_max_request_bytes: int = 1_048_576
    gateway_external_required: bool = False
    gateway_oidc_subject_header: str = "x-auth-request-sub"
    gateway_oidc_email_header: str = "x-auth-request-email"
    gateway_mtls_subject_header: str = "x-forwarded-client-cert"
    gateway_quota_header: str = "x-harness-quota-subject"
    gateway_waf_header: str = "x-harness-waf-verdict"

    temporal_target: str = "127.0.0.1:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "harness-orchestration"
    temporal_enabled: bool = False
    temporal_worker_in_api: bool = False
    temporal_authoritative_execution: bool = False
    legacy_resume_worker_enabled: bool = True
    langgraph_enabled: bool = False
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_events_enabled: bool = False
    redis_event_stream: str = "harness:events"
    redis_event_ttl_seconds: int = 86_400
    redis_event_consumer_group: str = "harness-realtime"
    redis_event_dlq_stream: str = "harness:events:dlq"
    redis_event_retry_zset: str = "harness:events:retry"
    redis_event_max_retries: int = 5
    redis_event_pending_idle_ms: int = 30_000
    minio_endpoint: str = "http://127.0.0.1:9000"
    minio_bucket: str = "harness-artifacts"
    minio_access_key: str = "harness-admin"
    minio_secret_key: str = "harness-minio-local"
    minio_secure: bool = False
    runner_manager_url: str = "http://127.0.0.1:8099"
    runner_manager_host: str = "127.0.0.1"
    runner_manager_port: int = 8099
    runner_rpc_token: str = ""
    runner_image: str = "harness-runner@sha256:local-dev"
    runner_allowed_image_digests: str = "sha256:local-dev"
    runner_docker_binary: str = "docker"
    runner_max_concurrent_tasks: int = 2
    runner_memory_limit: str = "512m"
    runner_cpu_limit: float = 1.0
    runner_pids_limit: int = 256
    runner_seccomp_profile: str = "default"
    runner_apparmor_profile: str = ""
    runner_egress_policy: str = "none"
    runner_signature_required: bool = False
    ui_test_target_url: str = "http://host.docker.internal:5173"
    a2a_remote_agent_host: str = "127.0.0.1"
    a2a_remote_agent_port: int = 8101
    a2a_remote_agent_token: str = ""
    otel_enabled: bool = False
    otel_service_name: str = "harness-api"
    otel_exporter_otlp_endpoint: str = "http://127.0.0.1:4317"
    backup_rpo_minutes: int = 15
    backup_rto_minutes: int = 60

    @model_validator(mode="after")
    def validate_temporal_execution_mode(self) -> "RuntimeSettings":
        """Prevent enabling Temporal execution without its control plane."""
        if self.temporal_authoritative_execution and not self.temporal_enabled:
            raise ValueError(
                "HARNESS_TEMPORAL_AUTHORITATIVE_EXECUTION requires "
                "HARNESS_TEMPORAL_ENABLED=true"
            )
        return self

    @model_validator(mode="after")
    def validate_production_security(self) -> "RuntimeSettings":
        """Refuse an unauthenticated dev default in a production environment.

        ``dev`` mode silently maps every request without credentials to the
        built-in administrator. That is acceptable for local development but is
        a release-blocking exposure otherwise, so it must be an explicit opt-in.
        """
        if self.environment == "production" and self.security_mode == "dev":
            raise ValueError(
                "HARNESS_ENVIRONMENT=production requires HARNESS_SECURITY_MODE "
                "to be set to a non-'dev' value (e.g. 'api-key')"
            )
        return self

    @property
    def allowed_origins(self) -> list[str]:
        """Return normalized CORS origins from a comma-separated setting."""
        return [
            origin.strip()
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]
