from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from harness.agents_core import (
    AgentCore,
    AgentRequest,
    AgentRuntimeResult,
    LangGraphRuntime,
    LocalBaseAgentRuntime,
    NullCapabilities,
)
from harness.observability.context import ExecutionContext


class FakeRuntime:
    kind = "fake"

    def __init__(self, result=None, error=None) -> None:
        self.result = result or AgentRuntimeResult(message="hi", status="completed")
        self.error = error
        self.calls: list = []

    async def execute(self, request, ctx):
        self.calls.append((request, ctx))
        if self.error:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_agent_core_run_lifecycle():
    audit: list[str] = []

    async def fake_audit(event_type, **kwargs):
        audit.append(event_type)

    core = AgentCore(agent_id="a1", name="A1", runtime=FakeRuntime(), audit=fake_audit)

    outcome = await core.run(AgentRequest(message="hello", session_id="s1"))

    assert outcome.message == "hi"
    assert outcome.status == "completed"
    assert audit == ["agent.started", "agent.completed"]


@pytest.mark.asyncio
async def test_agent_core_error_normalization():
    audit: list[str] = []

    async def fake_audit(event_type, **kwargs):
        audit.append(event_type)

    core = AgentCore(
        agent_id="a1",
        name="A1",
        runtime=FakeRuntime(error=RuntimeError("boom")),
        audit=fake_audit,
    )

    outcome = await core.run(AgentRequest(message="hello", session_id="s1"))

    assert outcome.status == "failed"
    assert "boom" in outcome.message
    assert audit == ["agent.started", "agent.failed"]


@pytest.mark.asyncio
async def test_agent_core_without_audit_is_optional():
    core = AgentCore(agent_id="a1", name="A1", runtime=FakeRuntime())

    outcome = await core.run(AgentRequest(message="hi", session_id="s1"))

    assert outcome.message == "hi"


@pytest.mark.asyncio
async def test_local_base_agent_runtime_adapts_agent():
    class FakeAgent:
        async def process_structured(self, message, session_id):
            from harness.models.artifact import AgentOutcome

            return AgentOutcome(message=f"got:{message}", metadata={"session_id": session_id})

    runtime = LocalBaseAgentRuntime(FakeAgent())
    result = await runtime.execute(
        AgentRequest(message="x", session_id="s"),
        ExecutionContext(trace_id="t", span_id="p"),
    )

    assert result.message == "got:x"
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_null_capabilities_are_empty():
    caps = NullCapabilities()

    assert caps.recall("k", "d") == "d"
    assert await caps.query_knowledge("kb", "q") == []
    assert await caps.execute_tool("t", {}) == {"error": "no capabilities available: t"}
    assert await caps.semantic_search("q") == []


def _build_graph():
    class S(TypedDict):
        message: str
        output: str

    def upper(state):
        return {"output": state["message"].upper()}

    graph = StateGraph(S)
    graph.add_node("upper", upper)
    graph.add_edge(START, "upper")
    graph.add_edge("upper", END)
    return graph.compile()


@pytest.mark.asyncio
async def test_langgraph_runtime_runs_state_graph():
    runtime = LangGraphRuntime(_build_graph)

    result = await runtime.execute(
        AgentRequest(message="hi", session_id="s"),
        ExecutionContext(trace_id="t", span_id="p"),
    )

    assert result.message == "HI"
    assert result.metadata["framework"] == "langgraph"