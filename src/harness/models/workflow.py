"""Durable workflow, human task, and approval grant models."""

from enum import StrEnum
from typing import Any

from harness.models.common import HarnessBaseModel


class WorkflowState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    SUSPENDED = "suspended"
    RESUME_REQUESTED = "resume_requested"
    RECOVERY_REQUIRED = "recovery_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class WorkflowStepState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class HumanTaskState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    PARTIALLY_APPROVED = "partially_approved"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ApprovalGrantState(StrEnum):
    ACTIVE = "active"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    REVOKED = "revoked"


class WorkflowStepType(StrEnum):
    AGENT = "agent"
    TOOL = "tool"


class IdempotencyMode(StrEnum):
    SAFE_RETRY = "safe_retry"
    KEYED = "keyed"
    NON_IDEMPOTENT = "non_idempotent"


class WorkflowRecord(HarnessBaseModel):
    id: str
    session_id: str | None = None
    trace_id: str
    state: WorkflowState
    version: int
    current_step_id: str | None = None
    output_data: dict[str, Any] | None = None
    error: str | None = None


class SuspensionInfo(HarnessBaseModel):
    workflow_id: str
    step_id: str
    checkpoint_id: str
    human_task_id: str
    policy_decision_id: str
    required_approvals: int


class ExecutionSuspended(RuntimeError):
    """Non-error control flow indicating durable human approval is required."""

    def __init__(self, info: SuspensionInfo) -> None:
        self.info = info
        super().__init__(
            f"Workflow '{info.workflow_id}' suspended for human task "
            f"'{info.human_task_id}'"
        )
