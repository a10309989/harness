from harness.core.registry import AgentRegistry
from harness.api.routes import a2a
from harness.models.a2a import AgentCard
from harness.a2a.remote_agent import A2ARemoteAgentAdapter


async def test_import_remote_agents_registers_adapters(monkeypatch):
    registry = AgentRegistry()

    async def fake_import_remote_agents_from_endpoint(**kwargs):
        kwargs["registry"].register(
            A2ARemoteAgentAdapter(
                card=AgentCard(
                    agent_id="remote.qa_conversation",
                    name="Remote QA Conversation Agent",
                ),
                endpoint=kwargs["endpoint"],
            )
        )
        return ["remote.qa_conversation"]

    monkeypatch.setattr(
        a2a,
        "import_remote_agents_from_endpoint",
        fake_import_remote_agents_from_endpoint,
    )

    response = await a2a.import_remote_agents(
        {"endpoint": "http://remote/a2a"},
        registry,
    )

    assert response["count"] == 1
    assert registry.get_agent("remote.qa_conversation").agent_name == "Remote QA Conversation Agent"
