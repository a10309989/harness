"""Tests for durable workflows, human tasks, grants, and resumption."""

from dataclasses import dataclass

import pytest

from harness.artifacts.service import ArtifactService
from harness.artifacts.storage import LocalCAS
from harness.models.policy import PolicyEffect, PolicyRuleInput, RiskLevel
from harness.models.tool import ToolResult
from harness.models.workflow import (
    ExecutionSuspended,
    IdempotencyMode,
)
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
from harness.workflow.runtime import ResumeWorker
from harness.workflow.service import WorkflowService


@pytest.fixture
async def phase3(db, tmp_path):
    audit = AuditService(db)
    artifacts = ArtifactService(db, LocalCAS(tmp_path / "artifacts"), audit)
    policy = PolicyEngine(db, audit)
    await policy.initialize()
    workflows = WorkflowService(db, artifacts, audit)
    tasks = HumanTaskService(db, workflows, artifacts, audit)

    requester = _actor("requester", "operator")
    token = set_execution_context(
        ExecutionContext(trace_id="phase3-trace", span_id="root", actor=requester)
    )
    yield db, policy, workflows, tasks
    reset_execution_context(token)


async def test_single_approval_suspends_and_grant_is_exactly_bound(phase3):
    db, policy, workflows, tasks = phase3
    workflow_id, executor, tool = await _suspend_high_risk_tool(
        workflows,
        policy,
        tasks,
        {"command": "deploy"},
    )
    workflow = await workflows.get(workflow_id)
    assert workflow["state"] == "suspended"
    assert workflow["steps"][0]["state"] == "waiting"

    task = (await tasks.list(workflow_id=workflow_id))[0]
    assert task["state"] == "pending"
    assert task["required_approvals"] == 1

    with pytest.raises(PermissionError, match="own task"):
        await tasks.approve(
            task["id"],
            idempotency_key="self-approval",
        )

    reviewer_token = _switch_actor(workflow_id, _actor("reviewer-1", "reviewer"))
    try:
        claimed = await tasks.claim(
            task["id"],
            idempotency_key="claim-1",
        )
        assert claimed["state"] == "claimed"
        approved = await tasks.approve(
            task["id"],
            comment="Reviewed exact deployment command.",
            idempotency_key="approve-1",
        )
        assert approved["state"] == "approved"
        repeated = await tasks.approve(
            task["id"],
            comment="duplicate request",
            idempotency_key="approve-1",
        )
        assert repeated["state"] == "approved"
    finally:
        reset_execution_context(reviewer_token)

    workflow = await workflows.get(workflow_id)
    grant = await tasks.get_active_grant(workflow_id)
    assert workflow["state"] == "resume_requested"
    assert grant is not None

    requester_token = _switch_actor(workflow_id, _actor("system-runner", "agent"))
    try:
        with pytest.raises(ValueError, match="tool input"):
            await executor.execute(
                "controlled_tool",
                {"command": "different"},
                approval_grant_id=grant["id"],
            )
        result = await executor.execute(
            "controlled_tool",
            {"command": "deploy"},
            approval_grant_id=grant["id"],
        )
        assert result.success is True
        assert tool.executions == 1
        with pytest.raises(ValueError, match="not active"):
            await executor.execute(
                "controlled_tool",
                {"command": "deploy"},
                approval_grant_id=grant["id"],
            )
    finally:
        reset_execution_context(requester_token)

    persisted = await db.fetch_one(
        "SELECT state FROM approval_grants WHERE id = $1",
        (grant["id"],),
    )
    assert persisted["state"] == "consumed"


async def test_two_person_approval_requires_distinct_actors(phase3):
    _, policy, workflows, tasks = phase3
    await policy.upsert_rule(
        PolicyRuleInput(
            name="controlled-two-person",
            priority=1,
            effect=PolicyEffect.REQUIRE_TWO_PERSON_APPROVAL,
            resource_pattern="controlled_tool",
            risk_levels=[RiskLevel.HIGH],
            reason="Two reviewers are required.",
        )
    )
    workflow_id, _, _ = await _suspend_high_risk_tool(
        workflows,
        policy,
        tasks,
        {"command": "production"},
    )
    task = (await tasks.list(workflow_id=workflow_id))[0]
    assert task["required_approvals"] == 2

    first_token = _switch_actor(workflow_id, _actor("reviewer-1", "reviewer"))
    try:
        first = await tasks.approve(
            task["id"],
            idempotency_key="approval-one",
        )
        assert first["state"] == "partially_approved"
        assert await tasks.get_active_grant(workflow_id) is None
        with pytest.raises(ValueError, match="cannot approve.*twice"):
            await tasks.approve(
                task["id"],
                idempotency_key="approval-one-again",
            )
    finally:
        reset_execution_context(first_token)

    second_token = _switch_actor(workflow_id, _actor("reviewer-2", "reviewer"))
    try:
        second = await tasks.approve(
            task["id"],
            idempotency_key="approval-two",
        )
        assert second["state"] == "approved"
    finally:
        reset_execution_context(second_token)

    assert (await workflows.get(workflow_id))["state"] == "resume_requested"
    assert await tasks.get_active_grant(workflow_id) is not None


async def test_rejection_cancels_workflow(phase3):
    _, policy, workflows, tasks = phase3
    workflow_id, _, _ = await _suspend_high_risk_tool(
        workflows,
        policy,
        tasks,
        {"command": "delete"},
    )
    task = (await tasks.list(workflow_id=workflow_id))[0]

    reviewer_token = _switch_actor(workflow_id, _actor("reviewer-1", "reviewer"))
    try:
        rejected = await tasks.reject(
            task["id"],
            comment="Unsafe operation.",
            idempotency_key="reject-1",
        )
    finally:
        reset_execution_context(reviewer_token)

    assert rejected["state"] == "rejected"
    assert (await workflows.get(workflow_id))["state"] == "cancelled"


async def test_resume_worker_reconstructs_from_checkpoint(phase3):
    _, policy, workflows, tasks = phase3
    workflow_id, executor, tool = await _suspend_high_risk_tool(
        workflows,
        policy,
        tasks,
        {"command": "resume-me"},
    )
    task = (await tasks.list(workflow_id=workflow_id))[0]
    reviewer_token = _switch_actor(workflow_id, _actor("reviewer-1", "reviewer"))
    try:
        await tasks.approve(
            task["id"],
            idempotency_key="worker-approval",
        )
    finally:
        reset_execution_context(reviewer_token)

    agent = _FakeAgent(executor)
    registry = _FakeRegistry(agent)
    sessions = _FakeSessions()
    worker = ResumeWorker(
        workflows,
        tasks,
        registry,
        sessions,
        poll_interval=0.01,
    )

    assert await worker.drain_once() == 1
    workflow = await workflows.get(workflow_id)
    assert workflow["state"] == "completed"
    assert workflow["steps"][0]["state"] == "completed"
    assert tool.executions == 1
    assert agent.resumptions == 1
    assert sessions.turns[0][1] == "assistant"
    assert await worker.drain_once() == 0


async def _suspend_high_risk_tool(workflows, policy, tasks, params):
    workflow_id = await workflows.create_run(
        session_id="session-1",
        input_data={"message": "run controlled tool"},
    )
    token = _switch_actor(workflow_id, _actor("requester", "operator"))
    registry = ToolRegistry()
    tool = _ControlledTool()
    registry.register(tool)
    executor = ToolExecutor(
        registry,
        runtime_services=RuntimeServices(
            policy=policy,
            workflows=workflows,
            human_tasks=tasks,
        ),
    )
    try:
        with agent_execution_scope(
            "controlled-agent",
            "run controlled tool",
            "session-1",
        ):
            with pytest.raises(ExecutionSuspended):
                await executor.execute("controlled_tool", params)
    finally:
        reset_execution_context(token)
    return workflow_id, executor, tool


def _switch_actor(workflow_id: str, actor: ActorContext):
    return set_execution_context(
        ExecutionContext(
            trace_id="phase3-trace",
            span_id=f"span-{actor.actor_id}",
            session_id="session-1",
            workflow_id=workflow_id,
            actor=actor,
        )
    )


def _actor(actor_id: str, role: str) -> ActorContext:
    return ActorContext(
        actor_id=actor_id,
        actor_type=ActorType.USER,
        display_name=actor_id,
        roles=(role,),
        permissions=frozenset({"*"}),
    )


class _ControlledTool(BaseTool):
    name = "controlled_tool"
    description = "A high-risk test tool."
    parameters_schema = {"command": {"type": "string", "required": True}}
    risk_level = RiskLevel.HIGH
    risk_tags = frozenset({"controlled"})
    idempotency_mode = IdempotencyMode.NON_IDEMPOTENT

    def __init__(self) -> None:
        self.executions = 0

    async def execute(self, **kwargs) -> ToolResult:
        self.executions += 1
        return ToolResult(success=True, data={"command": kwargs["command"]})


class _FakeAgent:
    agent_id = "controlled-agent"

    def __init__(self, executor: ToolExecutor) -> None:
        self.tool_executor = executor
        self.resumptions = 0

    async def resume_after_tool(self, **kwargs) -> str:
        self.resumptions += 1
        return f"resumed: {kwargs['tool_result'].data['command']}"


@dataclass
class _FakeRegistry:
    agent: _FakeAgent

    def get_agent(self, agent_id: str):
        if agent_id != self.agent.agent_id:
            raise KeyError(agent_id)
        return self.agent


class _FakeSessions:
    def __init__(self) -> None:
        self.turns = []

    def add_turn(self, *args) -> None:
        self.turns.append(args)
