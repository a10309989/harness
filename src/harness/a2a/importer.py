"""Import remote A2A agent cards into the local AgentRegistry."""

from __future__ import annotations

import httpx

from harness.a2a.remote_agent import A2ARemoteAgentAdapter
from harness.core.registry import AgentRegistry
from harness.models.a2a import AgentCard


async def import_remote_agents_from_endpoint(
    *,
    endpoint: str,
    registry: AgentRegistry,
    token: str = "",
    timeout_seconds: float = 30,
    skip_agent_ids: set[str] | None = None,
    artifact_service=None,
    db=None,
) -> list[str]:
    """Import remote A2A agent cards into the local AgentRegistry.

    ``skip_agent_ids`` protects locally-wired agents (which stay local, e.g.
    master/planner) from being replaced by remote A2A proxies. ``artifact_service``
    lets the adapter persist remote-produced artifacts into the local store.
    """
    endpoint = endpoint.rstrip("/")
    cards_url = endpoint.removesuffix("/a2a").rstrip("/") + "/.well-known/agent-cards"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.get(cards_url, headers=headers)
        response.raise_for_status()
        payload = response.json()
    imported = []
    for raw_card in payload.get("agents", []):
        card = AgentCard.model_validate(raw_card)
        if skip_agent_ids and card.agent_id in skip_agent_ids:
            continue
        registry.register(
            A2ARemoteAgentAdapter(
                card=card,
                endpoint=endpoint,
                token=token,
                timeout_seconds=timeout_seconds,
                artifact_service=artifact_service,
            )
        )
        if db is not None:
            # Multi-version routing (AB/canary): record the version endpoint.
            from harness.core.agent_router import register_endpoint

            await register_endpoint(db, card.agent_id, card.version or "1.0.0", endpoint)
        imported.append(card.agent_id)
    return imported
