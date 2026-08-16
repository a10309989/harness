"""Serializable contracts passed between Temporal workflows and clients."""

from __future__ import annotations

from dataclasses import dataclass


def approval_temporal_workflow_id(workflow_id: str) -> str:
    """Return the stable Temporal workflow ID for a Harness approval."""
    return f"harness-approval-{workflow_id}"


def agent_temporal_workflow_id(workflow_id: str) -> str:
    return f"harness-agent-{workflow_id}"


@dataclass(frozen=True)
class AgentWorkflowInput:
    workflow_id: str
    agent_id: str
    message: str
    session_id: str
    trace_id: str


@dataclass(frozen=True)
class AgentWorkflowResult:
    workflow_id: str
    response: str


@dataclass(frozen=True)
class ApprovalWorkflowInput:
    workflow_id: str
    human_task_id: str
    trace_id: str
    session_id: str | None = None
    resume_with_temporal: bool = False


@dataclass(frozen=True)
class ApprovalDecision:
    decision: str
    task_id: str
    actor_id: str
    grant_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ApprovalWorkflowResult:
    workflow_id: str
    human_task_id: str
    decision: str
    grant_id: str | None = None
    reason: str | None = None
