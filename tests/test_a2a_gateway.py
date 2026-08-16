from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from harness.a2a.remote_agent import A2ARemoteAgentAdapter
from harness.api.routes.a2a import jsonrpc, list_agent_cards
from harness.core.registry import AgentRegistry
from harness.models.a2a import A2AJsonRpcRequest, AgentCard
from harness.models.agent import AgentCapability, AgentState
from harness.models.artifact import AgentOutcome


class FakeA2AAgent:
    agent_id = "requirements-agent"
    agent_name = "Requirements Agent"
    state = AgentState.IDLE

    def __init__(self) -> None:
        self.config = SimpleNamespace(
            description="Analyzes requirements",
            execution_mode="plan",
            capabilities=[
                AgentCapability(
                    name="requirements.analysis",
                    description="Analyze product requirements",
                    input_schema={"type": "object", "required": ["message"]},
                    output_schema={"type": "object", "required": ["requirements"]},
                )
            ],
        )

    async def process_structured(self, message: str, session_id: str) -> AgentOutcome:
        return AgentOutcome(
            message=f"analyzed:{message}:{session_id}",
            metadata={"kind": "requirement_spec"},
        )


async def test_a2a_lists_agent_cards():
    registry = AgentRegistry()
    registry.register(FakeA2AAgent())

    response = await list_agent_cards(registry)

    assert response["agents"][0]["agent_id"] == "requirements-agent"
    assert response["agents"][0]["input_schema"]["required"] == ["message"]


async def test_a2a_jsonrpc_invokes_local_agent():
    registry = AgentRegistry()
    registry.register(FakeA2AAgent())

    response = await jsonrpc(
        A2AJsonRpcRequest(
            id="1",
            method="agent.invoke",
            params={
                "agent_id": "requirements-agent",
                "message": "login feature",
                "session_id": "session-1",
            },
        ),
        registry,
    )

    assert response.error is None
    assert response.result["status"] == "completed"
    assert response.result["message"] == "analyzed:login feature:session-1"


async def test_a2a_jsonrpc_returns_protocol_error_for_missing_agent():
    registry = AgentRegistry()

    response = await jsonrpc(
        A2AJsonRpcRequest(
            id="missing",
            method="agent.status",
            params={"agent_id": "missing-agent"},
        ),
        registry,
    )

    assert response.result is None
    assert response.error is not None
    assert response.error.code == -32004


async def test_remote_a2a_agent_adapter_invokes_jsonrpc_endpoint():
    remote_app = FastAPI()

    @remote_app.post("/a2a")
    async def remote_jsonrpc(request: dict):
        assert request["method"] == "agent.invoke"
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "agent_id": request["params"]["agent_id"],
                "session_id": request["params"]["session_id"],
                "status": "completed",
                "message": "remote-ok",
                "metadata": {"remote": True},
            },
        }

    adapter = A2ARemoteAgentAdapter(
        card=AgentCard(agent_id="remote-agent", name="Remote Agent"),
        endpoint="http://remote/a2a",
        transport=httpx.ASGITransport(app=remote_app),
    )

    outcome = await adapter.process_structured("hello", "session-1")

    assert outcome.message == "remote-ok"
    assert outcome.metadata["remote"] is True
    assert outcome.metadata["protocol"] == "a2a-jsonrpc"
