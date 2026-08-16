"""Optional LangGraph orchestration contained inside a Temporal Activity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from temporalio import activity

from harness.models.artifact import AgentOutcome


@dataclass(frozen=True)
class AgentRunInput:
    workflow_id: str | None
    agent_id: str
    message: str
    session_id: str


class AgentRunState(TypedDict):
    message: str
    session_id: str
    response: str
    outcome: AgentOutcome | None


class AgentExecutionActivities:
    """Executes an agent once; LangGraph never enters Workflow code."""

    def __init__(
        self,
        agent_registry,
        *,
        langgraph_enabled: bool,
        workflow_service=None,
        session_manager=None,
    ) -> None:
        self._agent_registry = agent_registry
        self._langgraph_enabled = langgraph_enabled
        self._workflow_service = workflow_service
        self._session_manager = session_manager

    @activity.defn(name="run_agent")
    async def run_agent(self, run_input: AgentRunInput) -> str:
        try:
            agent = self._agent_registry.get_agent(run_input.agent_id)
            if not self._langgraph_enabled:
                if hasattr(agent, "process_structured"):
                    outcome = await agent.process_structured(run_input.message, run_input.session_id)
                else:
                    outcome = AgentOutcome(
                        message=await agent.process(run_input.message, run_input.session_id)
                    )
            else:
                from langgraph.graph import END, START, StateGraph

                async def invoke_agent(state: AgentRunState) -> dict:
                    if hasattr(agent, "process_structured"):
                        outcome = await agent.process_structured(
                            state["message"], state["session_id"]
                        )
                    else:
                        outcome = AgentOutcome(
                            message=await agent.process(
                                state["message"], state["session_id"]
                            )
                        )
                    return {
                        "outcome": outcome,
                    }

                graph = StateGraph(AgentRunState)
                graph.add_node("agent", invoke_agent)
                graph.add_edge(START, "agent")
                graph.add_edge("agent", END)
                result = await graph.compile().ainvoke(
                    {
                        "message": run_input.message,
                        "session_id": run_input.session_id,
                        "response": "",
                        "outcome": None,
                    }
                )
                outcome = result["outcome"]

            response = outcome.message

            if self._workflow_service is not None and run_input.workflow_id:
                await self._workflow_service.complete(
                    run_input.workflow_id,
                    {
                        "message": response,
                        "artifacts": [artifact.model_dump(mode="json") for artifact in outcome.artifacts],
                        "artifact_cards": [card.model_dump(mode="json") for card in outcome.artifact_cards],
                        "metadata": outcome.metadata,
                    },
                )
                await self._workflow_service.update_temporal_state(
                    run_input.workflow_id, "completed"
                )
            if self._session_manager is not None:
                await self._session_manager.add_turn_durable(
                    run_input.session_id,
                    "assistant",
                    response,
                    {
                        "artifacts": [artifact.model_dump(mode="json") for artifact in outcome.artifacts],
                        "artifact_cards": [card.model_dump(mode="json") for card in outcome.artifact_cards],
                        "outcome_metadata": outcome.metadata,
                        "workflow_id": run_input.workflow_id,
                    },
                )
            return response
        except Exception as exc:
            if self._workflow_service is not None and run_input.workflow_id:
                await self._workflow_service.fail(run_input.workflow_id, str(exc))
                await self._workflow_service.update_temporal_state(
                    run_input.workflow_id, "failed"
                )
            raise
