"""Master-driven DAG execution: planner generates, master executes, durably.

Verifies the design: the planner only *proposes* the DAG; the master owns
execution. When a node suspends for human approval, the plan + completed
outputs are checkpointed, and an approved resume continues the remaining DAG.
"""

from __future__ import annotations

import pytest

from harness.artifacts.service import ArtifactService
from harness.artifacts.storage import LocalCAS
from harness.core.events import EventBus
from harness.core.master import MasterOrchestrator
from harness.core.registry import AgentRegistry
from harness.models.artifact import AgentOutcome
from harness.models.policy import RiskLevel
from harness.models.tool import ToolResult
from harness.models.workflow import ExecutionSuspended
from harness.observability.audit import AuditService
from harness.observability.context import (
    ExecutionContext,
    reset_execution_context,
    set_execution_context,
)
from harness.policy.engine import PolicyEngine
from harness.runtime.services import RuntimeServices
from harness.security.models import ActorContext, ActorType
from harness.tools.base import BaseTool
from harness.tools.executor import ToolExecutor
from harness.tools.registry import ToolRegistry
from harness.workflow.context import agent_execution_scope
from harness.workflow.human_tasks import HumanTaskService
from harness.workflow.resumer import ApprovedToolResumer
from harness.workflow.service import WorkflowService


class DbWriteTool(BaseTool):
    name = "db_write"
    description = "high risk write"
    parameters_schema = {"sql": {"type": "string", "required": True}}
    risk_level = RiskLevel.HIGH

    async def execute(self, **kwargs):
        return ToolResult(success=True, data={"ok": True})


class SuspendingAgent:
    """Local node agent whose tool call suspends for approval."""

    agent_id = "agent_a"
    agent_name = "Agent A"

    def __init__(self, tool_executor: ToolExecutor) -> None:
        self.tool_executor = tool_executor

    async def process_structured(self, prompt, session_id):
        with agent_execution_scope(self.agent_id, prompt, session_id):
            await self.tool_executor.execute("db_write", {"sql": "DELETE"})
            return AgentOutcome(message="unreachable")  # pragma: no cover

    async def resume_after_tool(
        self,
        *,
        original_message: str,
        tool_name: str,
        tool_result: ToolResult,
        session_id: str,
    ) -> str:
        return "A-resumed"


class NormalAgent:
    agent_id = "agent_b"
    agent_name = "Agent B"

    def __init__(self) -> None:
        self.calls = 0

    async def process_structured(self, prompt, session_id):
        self.calls += 1
        return AgentOutcome(message="B-result")


class FakeSessionManager:
    def add_turn(self, *args, **kwargs) -> None:
        pass


class RegistryPlanner:
    agent_id = "planner"
    agent_name = "Remote Planner"

    async def process_structured(self, message, session_id):
        return AgentOutcome(
            message="plan",
            status="completed",
            metadata={
                "planned": True,
                "plan": {
                    "schema_version": 1,
                    "objective": "flow",
                    "nodes": [
                        {
                            "id": "a",
                            "node_type": "agent",
                            "description": "step a",
                            "depends_on": [],
                            "agent_id": "agent_a",
                            "input_data": {},
                        },
                        {
                            "id": "b",
                            "node_type": "agent",
                            "description": "step b",
                            "depends_on": ["a"],
                            "agent_id": "agent_b",
                            "input_data": {},
                        },
                    ],
                },
            },
        )


async def _approve(tasks: HumanTaskService, task_id: str) -> None:
    token = set_execution_context(
        ExecutionContext(
            trace_id="t",
            span_id="s",
            actor=ActorContext(
                actor_id="bob",
                actor_type=ActorType.USER,
                tenant_id="default",
            ),
        )
    )
    try:
        await tasks.approve(task_id, idempotency_key="k1")
    finally:
        reset_execution_context(token)


async def test_master_drives_dag_and_resume_continues_remaining_nodes(db, tmp_path):
    audit = AuditService(db)
    artifacts = ArtifactService(db, LocalCAS(tmp_path / "artifacts"), audit)
    policy = PolicyEngine(db, audit)
    await policy.initialize()
    workflows = WorkflowService(db, artifacts, audit)
    tasks = HumanTaskService(db, workflows, artifacts, audit)

    treg = ToolRegistry()
    treg.register(DbWriteTool())
    executor = ToolExecutor(
        treg,
        runtime_services=RuntimeServices(
            policy=policy, workflows=workflows, human_tasks=tasks
        ),
    )
    agent_a = SuspendingAgent(executor)
    agent_b = NormalAgent()
    registry = AgentRegistry()
    registry.register(agent_a)
    registry.register(agent_b)

    registry.register(RegistryPlanner())
    master = MasterOrchestrator(registry, EventBus())
    master.runtime_services = RuntimeServices(
        policy=policy, workflows=workflows, human_tasks=tasks
    )

    wf_id = await workflows.create_run(session_id="s1", input_data={"message": "run"})
    token = set_execution_context(
        ExecutionContext(trace_id="t", span_id="s", session_id="s1", workflow_id=wf_id)
    )
    try:
        with pytest.raises(ExecutionSuspended) as excinfo:
            await master.process_structured("先分析需求，然后生成完整流程", "s1")

        # Durable plan checkpoint: plan + completed outputs persisted.
        _, checkpoint = await workflows.load_checkpoint(wf_id)
        assert checkpoint["kind"] == "plan_execution"
        assert checkpoint["suspended_node_id"] == "a"
        assert checkpoint["suspended_agent_id"] == "agent_a"

        # Bridge/route moves the master workflow to SUSPENDED; approve it.
        await workflows.suspend(wf_id)
        await _approve(tasks, excinfo.value.info.human_task_id)

        resumer = ApprovedToolResumer(workflows, tasks, registry, FakeSessionManager())
        await resumer.resume(wf_id)
    finally:
        reset_execution_context(token)

    assert agent_b.calls == 1, "remaining DAG node 'b' should run after resume"
    record = await workflows.get(wf_id)
    assert record["state"] == "completed"
    assert record["output_data"]["message"] == "B-result"
