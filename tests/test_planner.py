"""Tests for constrained planning and deterministic DAG execution."""

import pytest

from harness.core.events import EventBus
from harness.core.master import MasterOrchestrator
from harness.core.planner import PlanCompiler
from harness.core.registry import AgentRegistry
from harness.models.plan import ExecutionPlan, PlanNode


class _Agent:
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.agent_name = agent_id
        self.config = object()
        self.calls: list[str] = []

    async def process(self, message: str, session_id: str) -> str:
        self.calls.append(message)
        return f"{self.agent_id}:{len(self.calls)}"


def test_plan_rejects_cycles():
    with pytest.raises(ValueError, match="cycle"):
        ExecutionPlan(
            objective="cycle",
            nodes=[
                PlanNode(
                    id="a",
                    description="a",
                    agent_id="one",
                    depends_on=["b"],
                ),
                PlanNode(
                    id="b",
                    description="b",
                    agent_id="two",
                    depends_on=["a"],
                ),
            ],
        )


def test_compiler_rejects_unregistered_agents():
    registry = AgentRegistry()
    compiler = PlanCompiler(registry)
    plan = ExecutionPlan(
        objective="unknown",
        nodes=[
            PlanNode(
                id="step_1",
                description="unknown",
                agent_id="missing",
            )
        ],
    )
    with pytest.raises(ValueError, match="unavailable agent"):
        compiler.compile(plan)


async def test_planner_executes_dependency_order_and_passes_outputs():
    """The planner generates the DAG; the master-owned executor drives it."""
    from harness.core.execution import PlanExecutor

    registry = AgentRegistry()
    first = _Agent("requirements_analyst")
    second = _Agent("test_case_generator")
    registry.register(first)
    registry.register(second)

    plan = ExecutionPlan(
        objective="prepare tests",
        nodes=[
            PlanNode(
                id="requirements",
                description="analyze requirements",
                agent_id="requirements_analyst",
            ),
            PlanNode(
                id="cases",
                description="generate cases",
                depends_on=["requirements"],
                agent_id="test_case_generator",
            ),
        ],
    )
    plan = PlanCompiler(registry).compile(plan)

    result = await PlanExecutor(registry).execute(plan, session_id="s1")

    assert result.final_output == "test_case_generator:1"
    assert "requirements_analyst:1" in second.calls[0]


async def test_master_uses_planner_only_for_complex_requests():
    registry = AgentRegistry()
    specialist = _Agent("requirements_analyst")
    registry.register(specialist)

    async def llm_call(_, __):
        return (
            '{"schema_version":1,"objective":"flow","nodes":['
            '{"id":"one","node_type":"agent","description":"analyze",'
            '"depends_on":[],"agent_id":"requirements_analyst",'
            '"input_data":{}}]}'
        )

    from harness.models.artifact import AgentOutcome

    class _Planner:
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
                                "id": "one",
                                "node_type": "agent",
                                "description": "analyze",
                                "depends_on": [],
                                "agent_id": "requirements_analyst",
                                "input_data": {},
                            }
                        ],
                    },
                },
            )

    registry.register(_Planner())
    master = MasterOrchestrator(
        registry,
        EventBus(),
        llm_text_call=llm_call,
    )

    result = await master.process(
        "先分析需求，然后生成完整流程",
        "s1",
    )

    assert result == "requirements_analyst:1"
    assert master.get_state("last_plan")["schema_version"] == 1


async def test_master_resolves_planner_from_registry():
    """The planner is now a registry agent (remote A2A); master calls it there."""
    from harness.models.artifact import AgentOutcome

    registry = AgentRegistry()
    specialist = _Agent("requirements_analyst")
    registry.register(specialist)

    class _Planner:
        agent_id = "planner"
        agent_name = "Planner"

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
                                "id": "one",
                                "node_type": "agent",
                                "description": "analyze",
                                "depends_on": [],
                                "agent_id": "requirements_analyst",
                                "input_data": {},
                            }
                        ],
                    },
                },
            )

    registry.register(_Planner())

    async def llm_call(_, __):
        return "{}"

    # No planner= passed → resolved from the registry (remote-style).
    master = MasterOrchestrator(registry, EventBus(), llm_text_call=llm_call)

    result = await master.process("先分析需求，然后生成完整流程", "s1")
    assert result == "requirements_analyst:1"
    assert master.get_state("last_plan")["objective"] == "flow"

