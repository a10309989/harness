"""Shared runtime services passed explicitly to agents and executors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.artifacts.service import ArtifactService
    from harness.knowledge.service import KnowledgeService
    from harness.observability.agent_events import AgentEventService
    from harness.observability.audit import AuditService
    from harness.policy.engine import PolicyEngine
    from harness.workflow.human_tasks import HumanTaskService
    from harness.workflow.service import WorkflowService


@dataclass(slots=True)
class RuntimeServices:
    """Governance services required by an Agent runtime.

    Fields are optional so lightweight agents and isolated unit tests can use
    only the capabilities they need.
    """

    audit: AuditService | None = None
    agent_events: AgentEventService | None = None
    artifacts: ArtifactService | None = None
    knowledge: KnowledgeService | None = None
    policy: PolicyEngine | None = None
    workflows: WorkflowService | None = None
    human_tasks: HumanTaskService | None = None
