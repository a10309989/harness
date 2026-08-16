import pytest

from harness.temporal.activities import ApprovalActivities
from harness.temporal.models import ApprovalDecision, ApprovalWorkflowInput


class FakeAuditService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def record(self, event_type: str, **kwargs) -> str:
        self.calls.append({"event_type": event_type, **kwargs})
        return "event-id"


@pytest.mark.asyncio
async def test_record_approval_waiting_writes_audit_event():
    audit = FakeAuditService()
    activities = ApprovalActivities(audit)

    await activities.record_approval_waiting(
        ApprovalWorkflowInput(
            workflow_id="wf-1",
            human_task_id="task-1",
            trace_id="trace-1",
        )
    )

    assert len(audit.calls) == 1
    call = audit.calls[0]
    assert call["event_type"] == "temporal.approval_waiting"
    assert call["action"] == "temporal.approval.waiting"
    assert call["resource_type"] == "human_task"
    assert call["resource_id"] == "task-1"
    assert call["metadata"] == {
        "workflow_id": "wf-1",
        "human_task_id": "task-1",
        "trace_id": "trace-1",
    }


@pytest.mark.asyncio
async def test_record_approval_decision_writes_audit_event():
    audit = FakeAuditService()
    activities = ApprovalActivities(audit)

    await activities.record_approval_decision(
        ApprovalDecision(
            decision="approved",
            task_id="task-1",
            actor_id="user-1",
            grant_id="grant-1",
            reason="looks good",
        )
    )

    assert len(audit.calls) == 1
    call = audit.calls[0]
    assert call["event_type"] == "temporal.approval_decision"
    assert call["action"] == "temporal.approval.approved"
    assert call["decision"] == "approved"
    assert call["resource_id"] == "task-1"
    assert call["metadata"]["actor_id"] == "user-1"
    assert call["metadata"]["grant_id"] == "grant-1"


@pytest.mark.asyncio
async def test_activities_without_audit_service_do_not_raise():
    # Standalone worker path: no audit service wired, must not crash.
    activities = ApprovalActivities(None)

    await activities.record_approval_waiting(
        ApprovalWorkflowInput(workflow_id="wf-1", human_task_id="task-1", trace_id="trace-1")
    )
    await activities.record_approval_decision(
        ApprovalDecision(decision="rejected", task_id="task-1", actor_id="user-1")
    )