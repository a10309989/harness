import httpx

from harness.a2a.example_agents import create_example_agents
from harness.a2a.remote_agent import A2ARemoteAgentAdapter
from harness.a2a.server import create_a2a_agent_app
from harness.models.a2a import AgentCard


async def test_remote_a2a_service_discovers_cards_and_invokes_agent():
    app = create_a2a_agent_app(create_example_agents())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://remote") as client:
        cards = await client.get("/.well-known/agent-cards")
        assert cards.status_code == 200
        assert len(cards.json()["agents"]) == 7

        response = await client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "invoke-1",
                "method": "agent.invoke",
                "params": {
                    "agent_id": "remote.requirements",
                    "message": "用户可以登录系统",
                    "session_id": "session-1",
                },
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["result"]["status"] == "completed"
    assert payload["result"]["metadata"]["contract"] == "RequirementAnalysisPackage"


async def test_remote_a2a_service_enforces_bearer_token():
    app = create_a2a_agent_app(create_example_agents(), auth_token="secret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://remote") as client:
        denied = await client.get("/.well-known/agent-cards")
        allowed = await client.get(
            "/.well-known/agent-cards",
            headers={"Authorization": "Bearer secret"},
        )

    assert denied.status_code == 401
    assert allowed.status_code == 200


async def test_remote_adapter_invokes_example_service():
    app = create_a2a_agent_app(create_example_agents())
    adapter = A2ARemoteAgentAdapter(
        card=AgentCard(agent_id="remote.diagnosis", name="Remote Diagnosis Agent"),
        endpoint="http://remote/a2a",
        transport=httpx.ASGITransport(app=app),
    )

    outcome = await adapter.process_structured("diagnose run", "session-1")

    assert outcome.status == "completed"
    assert outcome.metadata["contract"] == "DiagnosisReport"
    assert outcome.metadata["remote_agent_id"] == "remote.diagnosis"
