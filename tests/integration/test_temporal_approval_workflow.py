"""End-to-end Temporal approval workflow against a real Temporal server.

Skipped unless HARNESS_RUN_TEMPORAL_INTEGRATION=1 and a local Temporal server is
running (see deploy/temporal/docker-compose.yaml). Exercises the real
ApprovalActivities so the approval wait/decision flow is proven to write to the
durable audit chain, not just the log stream.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from temporalio.client import Client
from temporalio.worker import Worker

from harness.core.events import EventBus
from harness.observability.audit import AuditService
from harness.observability.outbox import OutboxWorker
from harness.runtime.settings import RuntimeSettings
from harness.temporal.activities import ApprovalActivities
from harness.temporal.models import ApprovalDecision, ApprovalWorkflowInput
from harness.temporal.workflows.approval import ApprovalWorkflow

pytestmark = pytest.mark.skipif(
    os.getenv("HARNESS_RUN_TEMPORAL_INTEGRATION") != "1",
    reason="requires a local Temporal server (deploy/temporal/docker-compose.yaml)",
)


async def test_temporal_approval_workflow_records_audit(db):
    settings = RuntimeSettings()
    audit = AuditService(db)
    outbox = OutboxWorker(db, audit, EventBus(), poll_interval=0.01)

    client = await Client.connect(
        settings.temporal_target,
        namespace=settings.temporal_namespace,
    )
    approval = ApprovalActivities(audit)
    workflow_id = f"harness-approval-itest-{uuid4().hex[:8]}"

    async with Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[ApprovalWorkflow],
        activities=[approval.record_approval_waiting, approval.record_approval_decision],
    ):
        handle = await client.start_workflow(
            ApprovalWorkflow.run,
            ApprovalWorkflowInput(
                workflow_id="wf-itest",
                human_task_id="task-itest",
                trace_id="trace-itest",
            ),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
        await handle.signal(
            "approve",
            ApprovalDecision(
                decision="approved",
                task_id="task-itest",
                actor_id="user-itest",
                grant_id="grant-itest",
            ),
        )
        result = await handle.result()
        assert result.decision == "approved"

    # Drain the transactional outbox so queued audit events land in the table.
    await outbox.drain_once(limit=100)

    rows = await db.fetch_all(
        "SELECT * FROM audit_events WHERE event_type = 'temporal.approval_decision'"
    )
    assert len(rows) == 1
    assert rows[0]["resource_id"] == "task-itest"
    # The durable wait boundary must also be present.
    waiting = await db.fetch_all(
        "SELECT * FROM audit_events WHERE event_type = 'temporal.approval_waiting'"
    )
    assert len(waiting) == 1