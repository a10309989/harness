"""Minimal durable Temporal workflow for a Harness human approval wait."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from harness.temporal.models import (
    ApprovalDecision,
    ApprovalWorkflowInput,
    ApprovalWorkflowResult,
)


@workflow.defn
class ApprovalWorkflow:
    """Wait for one terminal approval decision through a Temporal Signal."""

    def __init__(self) -> None:
        self._input: ApprovalWorkflowInput | None = None
        self._decision: ApprovalDecision | None = None

    @workflow.run
    async def run(self, workflow_input: ApprovalWorkflowInput) -> ApprovalWorkflowResult:
        self._input = workflow_input
        await workflow.execute_activity(
            "record_approval_waiting",
            workflow_input,
            start_to_close_timeout=timedelta(seconds=30),
        )
        await workflow.wait_condition(lambda: self._decision is not None)
        decision = self._decision
        assert decision is not None
        await workflow.execute_activity(
            "record_approval_decision",
            decision,
            start_to_close_timeout=timedelta(seconds=30),
        )
        if decision.decision == "approved" and workflow_input.resume_with_temporal:
            await workflow.execute_activity(
                "resume_approved_workflow",
                workflow_input.workflow_id,
                start_to_close_timeout=timedelta(minutes=15),
            )
        return ApprovalWorkflowResult(
            workflow_id=workflow_input.workflow_id,
            human_task_id=workflow_input.human_task_id,
            decision=decision.decision,
            grant_id=decision.grant_id,
            reason=decision.reason,
        )

    @workflow.signal(name="approve")
    async def approve(self, decision: ApprovalDecision) -> None:
        self._set_decision(decision, expected="approved")

    @workflow.signal(name="reject")
    async def reject(self, decision: ApprovalDecision) -> None:
        self._set_decision(decision, expected="rejected")

    @workflow.query(name="status")
    def status(self) -> dict[str, str | None]:
        return {
            "workflow_id": self._input.workflow_id if self._input else None,
            "human_task_id": self._input.human_task_id if self._input else None,
            "state": self._decision.decision if self._decision else "waiting_approval",
        }

    def _set_decision(self, decision: ApprovalDecision, *, expected: str) -> None:
        if decision.decision != expected:
            raise ValueError(f"Expected '{expected}' approval decision")
        if self._input is None or decision.task_id != self._input.human_task_id:
            raise ValueError("Approval decision does not match this human task")
        if self._decision is not None:
            if self._decision == decision:
                return
            raise ValueError("Approval decision has already been recorded")
        self._decision = decision
