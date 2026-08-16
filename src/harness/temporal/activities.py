"""Side-effecting Temporal Activities used by approval workflows."""

from __future__ import annotations

from temporalio import activity

from harness.temporal.models import ApprovalDecision, ApprovalWorkflowInput


class ApprovalActivities:
    """Activities that persist approval wait/decision records to the audit log.

    These replace the previous no-op log-only activities: every approval wait
    boundary and terminal decision is now written through the durable,
    tamper-evident audit chain, so Temporal history lines up with the audit
    trail even during shadow migration.
    """

    def __init__(self, audit_service=None) -> None:
        self._audit_service = audit_service

    @activity.defn(name="record_approval_waiting")
    async def record_approval_waiting(self, workflow_input: ApprovalWorkflowInput) -> None:
        """Record a durable approval wait through the audit service."""
        await self._record(
            "temporal.approval_waiting",
            action="temporal.approval.waiting",
            resource_id=workflow_input.human_task_id,
            metadata={
                "workflow_id": workflow_input.workflow_id,
                "human_task_id": workflow_input.human_task_id,
                "trace_id": workflow_input.trace_id,
            },
        )

    @activity.defn(name="record_approval_decision")
    async def record_approval_decision(self, decision: ApprovalDecision) -> None:
        """Record an approval decision before the Temporal workflow completes."""
        await self._record(
            "temporal.approval_decision",
            action=f"temporal.approval.{decision.decision}",
            resource_id=decision.task_id,
            decision=decision.decision,
            metadata={
                "task_id": decision.task_id,
                "decision": decision.decision,
                "actor_id": decision.actor_id,
                "grant_id": decision.grant_id,
            },
        )

    async def _record(
        self,
        event_type: str,
        *,
        action: str,
        resource_id: str,
        decision: str | None = None,
        metadata: dict,
    ) -> None:
        if self._audit_service is None:
            # Standalone worker without the domain runtime: fall back to a log
            # stream so the Activity still produces an observable boundary.
            activity.logger.info(
                "Harness approval activity: %s",
                action,
                extra=metadata,
            )
            return
        await self._audit_service.record(
            event_type,
            action=action,
            resource_type="human_task",
            resource_id=resource_id,
            decision=decision,
            metadata=metadata,
        )


class TemporalExecutionActivities:
    """Activities requiring access to the Harness domain runtime."""

    def __init__(self, resumer) -> None:
        self._resumer = resumer

    @activity.defn(name="resume_approved_workflow")
    async def resume_approved_workflow(self, workflow_id: str) -> None:
        await self._resumer.resume(workflow_id)