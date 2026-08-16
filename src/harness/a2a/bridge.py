"""Local→A2A bridge: expose local harness agents as A2A JSON-RPC services.

This is the second half of the A2A two-way bridge. The remote→local direction
already exists via ``A2ARemoteAgentAdapter`` + ``import_remote_agents_from_endpoint``.
Here we wrap any local ``BaseAgent`` (or ``AgentCore``) as a ``RemoteA2AAgent``
so it can be served by the existing ``create_a2a_agent_app`` and called by any
A2A client — including the harness itself.
"""

from __future__ import annotations

from typing import Any, Iterable

from harness.a2a.server import RemoteA2AAgent, completed_result, create_a2a_agent_app
from harness.agents_core.contracts import AgentCore
from harness.agents_core.migration import wrap_base_agent
from harness.models.a2a import A2AInvokeParams, A2AInvokeResult, AgentCard
from harness.models.artifact import AgentOutcome


def build_agent_card(
    *,
    agent_id: str,
    name: str,
    description: str = "",
    capabilities: list | None = None,
    risk_level: str = "low",
    metadata: dict[str, Any] | None = None,
) -> AgentCard:
    """Build an ``AgentCard`` from a local agent's identity and capabilities."""
    return AgentCard(
        agent_id=agent_id,
        name=name,
        description=description,
        capabilities=capabilities or [],
        protocols=["a2a-jsonrpc"],
        risk_level=risk_level,
        metadata=metadata or {},
    )


def _card_from_local_agent(agent: Any) -> AgentCard:
    """Derive an AgentCard from a local agent (AgentaCore or BaseAgent)."""
    agent_id = getattr(agent, "agent_id", "unknown")
    name = getattr(agent, "agent_name", None) or getattr(agent, "name", agent_id)
    description = getattr(agent, "description", "")
    if isinstance(agent, AgentCore):
        return build_agent_card(
            agent_id=agent.agent_id,
            name=agent.name,
            description=agent.description,
            metadata={"runtime": getattr(agent.runtime, "kind", "unknown")},
        )
    config = getattr(agent, "config", None)
    capabilities = getattr(config, "capabilities", None) if config else None
    return build_agent_card(
        agent_id=agent_id,
        name=name,
        description=description,
        capabilities=capabilities,
        metadata={"source": getattr(type(agent), "__name__", "BaseAgent")},
    )


class LocalAgentA2ABridge(RemoteA2AAgent):
    """Wraps a local agent so it can be invoked over A2A JSON-RPC."""

    def __init__(self, agent: Any) -> None:
        self._agent = agent
        super().__init__(_card_from_local_agent(agent))

    async def invoke(self, params: A2AInvokeParams) -> A2AInvokeResult:
        outcome = await self._invoke_local(params)
        artifacts = [
            {
                "artifact_id": ref.artifact_id,
                "version_id": ref.version_id,
                "version_number": ref.version_number,
                "content_digest": ref.content_digest,
                "media_type": ref.media_type,
                "size_bytes": ref.size_bytes,
            }
            for ref in getattr(outcome, "artifacts", []) or []
        ]
        return completed_result(
            params,
            outcome.message,
            metadata={"status": outcome.status, **(outcome.metadata or {})},
            artifacts=artifacts,
        )

    async def _invoke_local(self, params: A2AInvokeParams) -> AgentOutcome:
        if isinstance(self._agent, AgentCore):
            from harness.agents_core.contracts import AgentRequest

            return await self._agent.run(
                AgentRequest(
                    message=params.message,
                    session_id=params.session_id,
                    input_data=params.input_data,
                    trace_id=params.trace_id,
                    idempotency_key=params.idempotency_key,
                )
            )
        # Plain BaseAgent (or duck-typed agent exposing process_structured).
        return await self._agent.process_structured(params.message, params.session_id)


def create_local_a2a_app(
    agents: Iterable[Any],
    *,
    service_name: str = "Harness Local A2A Agents",
    auth_token: str = "",
):
    """Serve a collection of local agents as an A2A JSON-RPC FastAPI app."""
    bridges = [LocalAgentA2ABridge(agent) for agent in agents]
    return create_a2a_agent_app(
        bridges,
        service_name=service_name,
        auth_token=auth_token,
    )


async def _build_real_agents(db_path: str) -> list[AgentCore]:
    """Wire the local orchestration agent from config and wrap it in AgentCore.

    Reuses the same ``wire_all_agents`` path the API uses, so the A2A service
    exposes the local Master only. Specialized agents are remote A2A services.
    LLM resolution falls back to the demo provider when no API keys are configured.
    """
    from harness.api.config_store import ConfigStore
    from harness.api.wire_agents import wire_all_agents
    from harness.core.events import EventBus
    from harness.core.registry import AgentRegistry
    from harness.db import create_database
    from harness.runtime.services import RuntimeServices
    from harness.skills.registry import SkillRegistry

    db = create_database(db_path)
    await db.initialize()
    try:
        registry = AgentRegistry()
        await wire_all_agents(
            registry=registry,
            event_bus=EventBus(),
            skill_registry=SkillRegistry(),
            config_store=ConfigStore(db),
            runtime_services=RuntimeServices(),
        )
        wrapped = []
        for agent_id, agent in registry.get_all_agents().items():
            # Only BaseAgent-shaped agents migrate to AgentCore; the remote
            # planner remains a specialized orchestration component.
            # (and any non-process_structured helper) is not a callable A2A agent.
            if hasattr(agent, "process_structured"):
                wrapped.append(wrap_base_agent(agent))
        return wrapped
    finally:
        # The DB is only needed for LLM-provider resolution at build time; the
        # built agents resolve their router eagerly and don't need it at runtime.
        await db.close()


async def create_real_a2a_app(
    *,
    db_path: str = "data/harness-a2a.db",
    service_name: str = "Harness Real A2A Agents",
    auth_token: str = "",
):
    """Serve the real configured agents as an A2A JSON-RPC FastAPI app."""
    cores = await _build_real_agents(db_path)
    return create_local_a2a_app(
        cores,
        service_name=service_name,
        auth_token=auth_token,
    )
