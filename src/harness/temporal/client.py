"""Temporal client adapter used by HTTP routes during shadow migration."""

from __future__ import annotations

from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from harness.runtime.settings import RuntimeSettings
from harness.temporal.models import (
    AgentWorkflowInput,
    ApprovalDecision,
    ApprovalWorkflowInput,
    agent_temporal_workflow_id,
    approval_temporal_workflow_id,
)
from harness.temporal.workflows.approval import ApprovalWorkflow
from harness.temporal.workflows.agent import AgentExecutionWorkflow
from harness.workflow.service import WorkflowService


class TemporalApprovalClient:
    """Starts and signals Temporal approval workflows with stable IDs."""

    def __init__(
        self,
        client: Client,
        settings: RuntimeSettings,
        workflow_service: WorkflowService | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._workflow_service = workflow_service

    @classmethod
    async def connect(
        cls,
        settings: RuntimeSettings,
        workflow_service: WorkflowService | None = None,
    ) -> "TemporalApprovalClient":
        client = await Client.connect(
            settings.temporal_target,
            namespace=settings.temporal_namespace,
        )
        return cls(client, settings, workflow_service)

    async def start(self, workflow_input: ApprovalWorkflowInput) -> str:
        temporal_workflow_id = approval_temporal_workflow_id(workflow_input.workflow_id)
        try:
            handle = await self._client.start_workflow(
                ApprovalWorkflow.run,
                workflow_input,
                id=temporal_workflow_id,
                task_queue=self._settings.temporal_task_queue,
            )
        except WorkflowAlreadyStartedError:
            handle = self._client.get_workflow_handle(temporal_workflow_id)
        await self._project(
            workflow_input.workflow_id,
            temporal_workflow_id=temporal_workflow_id,
            temporal_run_id=handle.result_run_id,
            state="waiting_approval",
            execution_engine=self._execution_engine,
        )
        return temporal_workflow_id

    async def start_agent(self, workflow_input: AgentWorkflowInput) -> str:
        temporal_workflow_id = agent_temporal_workflow_id(workflow_input.workflow_id)
        try:
            handle = await self._client.start_workflow(
                AgentExecutionWorkflow.run,
                workflow_input,
                id=temporal_workflow_id,
                task_queue=self._settings.temporal_task_queue,
            )
        except WorkflowAlreadyStartedError:
            handle = self._client.get_workflow_handle(temporal_workflow_id)
        await self._project(
            workflow_input.workflow_id,
            temporal_workflow_id=temporal_workflow_id,
            temporal_run_id=handle.result_run_id,
            state="running",
            execution_engine="temporal",
        )
        return temporal_workflow_id

    async def query_agent(self, workflow_id: str) -> dict:
        handle = self._client.get_workflow_handle(agent_temporal_workflow_id(workflow_id))
        return await handle.query("status")

    async def cancel_agent(self, workflow_id: str) -> None:
        handle = self._client.get_workflow_handle(agent_temporal_workflow_id(workflow_id))
        await handle.cancel()

    async def approve(
        self,
        *,
        workflow_id: str,
        task_id: str,
        actor_id: str,
        grant_id: str,
    ) -> None:
        await self._signal(
            workflow_id,
            "approve",
            ApprovalDecision(
                decision="approved",
                task_id=task_id,
                actor_id=actor_id,
                grant_id=grant_id,
            ),
        )
        await self._project(
            workflow_id,
            state="approved",
            execution_engine=self._execution_engine,
        )

    async def reject(
        self,
        *,
        workflow_id: str,
        task_id: str,
        actor_id: str,
        reason: str,
    ) -> None:
        await self._signal(
            workflow_id,
            "reject",
            ApprovalDecision(
                decision="rejected",
                task_id=task_id,
                actor_id=actor_id,
                reason=reason,
            ),
        )
        await self._project(
            workflow_id,
            state="rejected",
            execution_engine=self._execution_engine,
        )

    @property
    def _execution_engine(self) -> str:
        return "temporal" if self._settings.temporal_authoritative_execution else "legacy"

    async def _signal(self, workflow_id: str, signal: str, decision: ApprovalDecision) -> None:
        handle = self._client.get_workflow_handle(approval_temporal_workflow_id(workflow_id))
        await handle.signal(signal, decision)

    async def _project(
        self,
        workflow_id: str,
        *,
        temporal_workflow_id: str | None = None,
        temporal_run_id: str | None = None,
        state: str,
        execution_engine: str,
    ) -> None:
        if self._workflow_service is None:
            return
        await self._workflow_service.project_temporal_run(
            workflow_id,
            temporal_workflow_id=(
                temporal_workflow_id or approval_temporal_workflow_id(workflow_id)
            ),
            temporal_run_id=temporal_run_id,
            temporal_state=state,
            execution_engine=execution_engine,
        )
