"""Execution models."""

from datetime import datetime
from enum import StrEnum

from harness.models.common import HarnessBaseModel, TimestampedModel


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ExecutionResult(TimestampedModel):
    """Result of a single test script execution."""

    id: str
    execution_request_id: str
    script_id: str
    test_case_id: str
    status: ExecutionStatus
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_seconds: float | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    logs_path: str | None = None
    artifacts: dict[str, str] = {}
    assertions_total: int = 0
    assertions_passed: int = 0
    assertions_failed: int = 0
    retry_count: int = 0
    environment: dict[str, str] = {}


class ExecutionRequest(HarnessBaseModel):
    """Request to execute test scripts."""

    id: str = ""
    script_ids: list[str] = []
    schedule_type: str = "immediate"
    cron_expression: str | None = None
    trigger_event: str | None = None
    parameters: dict[str, str] = {}
    timeout_seconds: int = 3600
    max_retries: int = 0
    concurrency_limit: int = 1
    target_environment: str = "default"
    notify_on: list[ExecutionStatus] = [ExecutionStatus.FAILED, ExecutionStatus.ERROR]


class TestRun(TimestampedModel):
    """Aggregates multiple ExecutionResults into a test run."""

    id: str
    name: str
    execution_request_id: str
    results: list[ExecutionResult] = []
    summary: dict = {}
