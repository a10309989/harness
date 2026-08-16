"""Stateless master: submit_plan + WorkflowDriver advance a durable DAG to done.

The master API only *submits* a plan-run (create_run with agent_id=master +
plan in output_data) and returns a run_id. A WorkflowDriver worker picks up
RUNNING runs and advances ready nodes, persisting progress per node so multiple
driver replicas are safe.
"""

from __future__ import annotations

from harness.artifacts.service import ArtifactService
from harness.artifacts.storage import LocalCAS
from harness.core.events import EventBus
from harness.core.execution import WorkflowDriver
from harness.core.master import MasterOrchestrator
from harness.core.registry import AgentRegistry
from harness.models.artifact import AgentOutcome
from harness.observability.audit import AuditService
from harness.observability.context import (
    ExecutionContext,
    reset_execution_context,
    set_execution_context,
)
from harness.runtime.services import RuntimeServices
from harness.workflow.human_tasks import HumanTaskService
from harness.workflow.service import WorkflowService


class DomainAgent:
    def __init__(self, agent_id: str, result: str) -> None:
        self.agent_id = agent_id
        self.agent_name = agent_id
        self.result = result
        self.calls = 0

    async def process_structured(self, prompt, session_id):
        self.calls += 1
        return AgentOutcome(message=f"{self.result}#{self.calls}")


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
                            "description": "analyze",
                            "depends_on": [],
                            "agent_id": "requirements_analyst",
                            "input_data": {},
                        },
                        {
                            "id": "b",
                            "node_type": "agent",
                            "description": "cases",
                            "depends_on": ["a"],
                            "agent_id": "test_case_generator",
                            "input_data": {},
                        },
                    ],
                },
            },
        )


async def _build(db, tmp_path):
    audit = AuditService(db)
    artifacts = ArtifactService(db, LocalCAS(tmp_path / "artifacts"), audit)
    workflows = WorkflowService(db, artifacts, audit)
    tasks = HumanTaskService(db, workflows, artifacts, audit)

    req = DomainAgent("requirements_analyst", "R")
    cases = DomainAgent("test_case_generator", "C")
    registry = AgentRegistry()
    registry.register(req)
    registry.register(cases)

    registry.register(RegistryPlanner())
    master = MasterOrchestrator(registry, EventBus())
    master.runtime_services = RuntimeServices(
        workflows=workflows, human_tasks=tasks, artifacts=artifacts, audit=audit
    )
    driver = WorkflowDriver(registry, workflows)
    return db, workflows, master, driver, req, cases


async def test_submit_plan_and_driver_completes_dag(db, tmp_path):
    _, workflows, master, driver, req, cases = await _build(db, tmp_path)

    token = set_execution_context(
        ExecutionContext(trace_id="t", span_id="s", session_id="s1")
    )
    try:
        result = await master.submit_plan("先分析需求，然后生成完整流程", "s1")
    finally:
        reset_execution_context(token)

    assert result["status"] == "running"
    run_id = result["run_id"]
    assert run_id is not None

    processed = await driver.drain_once()
    assert processed >= 1

    record = await workflows.get_internal(run_id)
    assert record["state"] == "completed"
    assert req.calls == 1 and cases.calls == 1
    # complete() replaces output_data with the run result.
    assert "C#1" in record["output_data"]["message"]


async def test_submit_plan_non_planworthy_routes_directly(db, tmp_path):
    _, workflows, master, driver, req, cases = await _build(db, tmp_path)

    token = set_execution_context(
        ExecutionContext(trace_id="t", span_id="s", session_id="s2")
    )
    try:
        result = await master.submit_plan("帮我分析日志", "s2")
    finally:
        reset_execution_context(token)

    # "帮我分析日志" is not plan-worthy → direct routing (no run).
    assert result["run_id"] is None
    assert result["status"] == "completed"
