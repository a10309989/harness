"""Agent Hub heartbeat poller.

Periodically polls every configured remote A2A service's ``/agent-status`` and
updates each imported remote agent's heartbeat snapshot (status, capabilities,
skills, runtime, MCP) so the Agent management UI can show live capabilities and
detect offline agents.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from harness.core.registry import AgentRegistry
from harness.db.protocols import DatabaseProtocol

logger = logging.getLogger(__name__)


class HeartbeatPoller:
    """Poll remote services' agent-status and refresh the Agent Hub."""

    def __init__(
        self,
        db: DatabaseProtocol,
        registry: AgentRegistry,
        interval_seconds: int = 30,
        miss_cycles: int = 3,
    ) -> None:
        self.db = db
        self.registry = registry
        self.interval_seconds = interval_seconds
        self.miss_cycles = miss_cycles
        self._miss_count: dict[str, int] = {}

    async def run(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:
                logger.exception("Heartbeat poll failed")
            await asyncio.sleep(self.interval_seconds)

    async def poll_once(self) -> None:
        rows = await self.db.fetch_all(
            "SELECT id, endpoint, token FROM remote_services WHERE enabled = 1"
        )
        seen_services: set[str] = set()
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            for row in rows:
                endpoint = row["endpoint"]
                base = endpoint.removesuffix("/a2a").rstrip("/")
                seen_services.add(base)
                try:
                    r = await client.get(f"{base}/agent-status")
                    if r.status_code != 200:
                        raise RuntimeError(f"HTTP {r.status_code}")
                    payload = r.json()
                    self._apply(payload.get("agents", []), base)
                    self._miss_count[base] = 0
                except Exception as exc:
                    logger.warning("Heartbeat for %s failed: %s", base, exc)
                    self._miss_count[base] = self._miss_count.get(base, 0) + 1
                    if self._miss_count[base] >= self.miss_cycles:
                        await self._mark_service_offline(base)

    def _apply(self, agents: list[dict], base: str) -> None:
        for agent_info in agents:
            agent_id = agent_info.get("agent_id")
            if not agent_id:
                continue
            try:
                agent = self.registry.get_agent(agent_id)
            except KeyError:
                continue  # not registered locally
            update = getattr(agent, "update_heartbeat", None)
            if update is None:
                continue
            # Only update adapters whose endpoint matches this service.
            adapter_endpoint = str(getattr(agent, "endpoint", "") or "").rstrip("/")
            if adapter_endpoint:
                adapter_base = adapter_endpoint.removesuffix("/a2a").rstrip("/")
                if adapter_base != base:
                    continue
            update(
                status=agent_info.get("status", "online"),
                capabilities=agent_info.get("capabilities") or [],
                skills=agent_info.get("skills") or [],
                runtime=agent_info.get("runtime", ""),
                mcp_servers=agent_info.get("mcp_servers") or [],
                tools=agent_info.get("tools") or [],
                prompts=agent_info.get("prompts") or [],
                knowledge_bases=agent_info.get("knowledge_bases") or [],
            )

    async def _mark_service_offline(self, base: str) -> None:
        for agent_id, agent in self.registry.get_all_agents().items():
            adapter_endpoint = getattr(agent, "endpoint", "")
            if adapter_endpoint and str(adapter_endpoint).rstrip("/") == base:
                mark = getattr(agent, "mark_offline", None)
                if mark is not None:
                    await mark()