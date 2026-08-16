import pytest

from harness.a2a.bridge import (
    LocalAgentA2ABridge,
    build_agent_card,
    create_local_a2a_app,
)
from harness.a2a.server import RemoteAgentRegistry, dispatch_a2a_request
from harness.models.a2a import A2AInvokeParams


class FakeAgent:
    def __init__(self, agent_id="local.fake", name="Fake Agent") -> None:
        self.agent_id = agent_id
        self.agent_name = name
        self.description = "A fake agent for tests"

    async def process_structured(self, message, session_id):
        from harness.models.artifact import AgentOutcome

        return AgentOutcome(message=f"done:{message}", status="completed", metadata={"session_id": session_id})


def test_build_agent_card():
    card = build_agent_card(agent_id="x", name="X", description="desc")

    assert card.agent_id == "x"
    assert card.name == "X"
    assert card.protocols == ["a2a-jsonrpc"]


@pytest.mark.asyncio
async def test_bridge_invoke_calls_local_agent():
    bridge = LocalAgentA2ABridge(FakeAgent())

    result = await bridge.invoke(
        A2AInvokeParams(agent_id="local.fake", message="hello", session_id="s1")
    )

    assert result.status == "completed"
    assert result.message == "done:hello"


@pytest.mark.asyncio
async def test_bridge_card_derived_from_agent():
    bridge = LocalAgentA2ABridge(FakeAgent())

    assert bridge.card.agent_id == "local.fake"
    assert bridge.card.name == "Fake Agent"


@pytest.mark.asyncio
async def test_a2a_dispatch_end_to_end():
    registry = RemoteAgentRegistry()
    registry.register(LocalAgentA2ABridge(FakeAgent()))

    result = await dispatch_a2a_request(
        "agent.invoke",
        {"agent_id": "local.fake", "message": "hi", "session_id": "s"},
        registry,
    )

    assert result["message"] == "done:hi"
    assert result["status"] == "completed"


def test_create_local_a2a_app_builds_fastapi_app():
    app = create_local_a2a_app([FakeAgent()])

    assert app.title == "Harness Local A2A Agents"
    assert app.state.agent_registry.get("local.fake") is not None


@pytest.mark.asyncio
async def test_create_real_a2a_app_builds_real_agents():
    from conftest import TEST_PG_URL
    from harness.a2a.bridge import create_real_a2a_app

    app = await create_real_a2a_app(db_path=TEST_PG_URL)
    agent_ids = {card.agent_id for card in app.state.agent_registry.cards()}

    assert agent_ids == {"master"}
