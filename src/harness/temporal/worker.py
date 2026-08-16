"""Standalone Temporal worker entry point for Harness orchestration queues."""

from __future__ import annotations

import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from harness.runtime.settings import RuntimeSettings
from harness.temporal.activities import ApprovalActivities
from harness.temporal.activities import TemporalExecutionActivities
from harness.temporal.agent_activity import AgentExecutionActivities
from harness.temporal.workflows.approval import ApprovalWorkflow
from harness.temporal.workflows.agent import AgentExecutionWorkflow


async def run_worker(settings: RuntimeSettings | None = None, *, resumer=None, agent_registry=None, workflow_service=None, session_manager=None, audit_service=None) -> None:
    """Run the Temporal Worker for the configured Harness task queue."""
    resolved = settings or RuntimeSettings()
    client = await Client.connect(
        resolved.temporal_target,
        namespace=resolved.temporal_namespace,
    )
    approval_activities = ApprovalActivities(audit_service)
    activities = [approval_activities.record_approval_waiting, approval_activities.record_approval_decision]
    if resumer is not None:
        activities.append(TemporalExecutionActivities(resumer).resume_approved_workflow)
    if agent_registry is not None:
        activities.append(
            AgentExecutionActivities(
                agent_registry,
                langgraph_enabled=resolved.langgraph_enabled,
                workflow_service=workflow_service,
                session_manager=session_manager,
            ).run_agent
        )
    worker = Worker(
        client,
        task_queue=resolved.temporal_task_queue,
        workflows=[ApprovalWorkflow, AgentExecutionWorkflow],
        activities=activities,
    )
    await worker.run()


async def run_application_worker(settings: RuntimeSettings | None = None) -> None:
    """Run a Temporal worker with the Harness domain services available."""
    from harness.api.app import create_app

    resolved = (settings or RuntimeSettings()).model_copy(
        update={
            "temporal_enabled": True,
            "temporal_worker_in_api": False,
            "legacy_resume_worker_enabled": False,
        }
    )
    app = create_app(resolved)
    async with app.router.lifespan_context(app):
        await run_worker(
            resolved,
            resumer=app.state.resume_worker.resumer,
            agent_registry=app.state.agent_registry,
            workflow_service=app.state.workflow_service,
            session_manager=app.state.session_manager,
            audit_service=app.state.audit_service,
        )


def main() -> None:
    asyncio.run(run_application_worker())


if __name__ == "__main__":
    main()
