from harness.temporal.agent_activity import AgentExecutionActivities, AgentRunInput


class FakeAgent:
    async def process(self, message, session_id):
        return f"{session_id}:{message}"


class FakeRegistry:
    def get_agent(self, agent_id):
        assert agent_id == "agent-1"
        return FakeAgent()


async def test_run_agent_activity_preserves_legacy_execution_when_disabled():
    activity = AgentExecutionActivities(FakeRegistry(), langgraph_enabled=False)

    result = await activity.run_agent(
        AgentRunInput(workflow_id=None, agent_id="agent-1", message="hello", session_id="session-1")
    )

    assert result == "session-1:hello"


async def test_run_agent_activity_uses_langgraph_when_enabled():
    activity = AgentExecutionActivities(FakeRegistry(), langgraph_enabled=True)

    result = await activity.run_agent(
        AgentRunInput(workflow_id=None, agent_id="agent-1", message="graph", session_id="session-1")
    )

    assert result == "session-1:graph"
