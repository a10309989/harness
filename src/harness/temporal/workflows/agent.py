"""Durable Temporal workflow for an Agent execution request."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from harness.temporal.agent_activity import AgentRunInput
from harness.temporal.models import AgentWorkflowInput, AgentWorkflowResult


@workflow.defn
class AgentExecutionWorkflow:
    """Keep orchestration durable while agent execution stays in an Activity."""

    def __init__(self) -> None:
        self._workflow_id: str | None = None
        self._state = "running"

    @workflow.run
    async def run(self, workflow_input: AgentWorkflowInput) -> AgentWorkflowResult:
        self._workflow_id = workflow_input.workflow_id
        response = await workflow.execute_activity(
            "run_agent",
            AgentRunInput(
                workflow_id=workflow_input.workflow_id,
                agent_id=workflow_input.agent_id,
                message=workflow_input.message,
                session_id=workflow_input.session_id,
            ),
            start_to_close_timeout=timedelta(minutes=15),
        )
        self._state = "completed"
        return AgentWorkflowResult(workflow_id=workflow_input.workflow_id, response=response)

    @workflow.query(name="status")
    def status(self) -> dict[str, str | None]:
        return {"workflow_id": self._workflow_id, "state": self._state}
