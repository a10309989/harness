"""AgentRegistry — sub-agent registration and discovery."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Registry for all agents in the framework.

    The Master Agent uses this to look up sub-agents by ID for routing.
    Agents register themselves with their config and runtime instance.
    """

    _agents: dict[str, Any]  # agent_id → agent instance
    _agent_configs: dict[str, Any]  # agent_id → AgentConfig
    _master_agent: Any | None

    def __init__(self) -> None:
        self._agents = {}
        self._agent_configs = {}
        self._master_agent = None

    def register(self, agent: Any) -> None:
        """Register an agent instance.

        Args:
            agent: A BaseAgent (or subclass) instance.
        """
        self._agents[agent.agent_id] = agent
        config = getattr(agent, "config", None)
        if config is not None:
            self._agent_configs[agent.agent_id] = config
        logger.info(f"Agent registered: {agent.agent_id} ({agent.agent_name})")

    def set_master_agent(self, agent: Any) -> None:
        """Set the master orchestrator agent.

        Args:
            agent: The MasterAgent instance.
        """
        self._master_agent = agent
        self.register(agent)

    def get_agent(self, agent_id: str) -> Any:
        """Get an agent instance by ID.

        Args:
            agent_id: The agent's unique ID.

        Returns:
            The agent instance.

        Raises:
            KeyError: If the agent is not found.
        """
        if agent_id not in self._agents:
            raise KeyError(f"Agent '{agent_id}' not found. Available: {self.list_agents()}")
        return self._agents[agent_id]

    def get_master_agent(self) -> Any:
        """Get the master orchestrator agent.

        Returns:
            The MasterAgent instance.

        Raises:
            RuntimeError: If no master agent is set.
        """
        if self._master_agent is None:
            raise RuntimeError("No master agent registered")
        return self._master_agent

    def list_agents(self) -> list[str]:
        """List all registered agent IDs."""
        return list(self._agents.keys())

    def get_all_agents(self) -> dict[str, Any]:
        """Get all agent instances.

        Returns:
            Dict mapping agent_id → agent instance.
        """
        return dict(self._agents)

    def unregister(self, agent_id: str) -> bool:
        """Remove an agent from the registry. Returns True if it existed."""
        removed = self._agents.pop(agent_id, None)
        if removed is not None:
            self._agent_configs.pop(agent_id, None)
            logger.info(f"Agent unregistered: {agent_id}")
        return removed is not None

    async def load_from_config(self, config_dir: str = "config/agents") -> None:
        """Load all agents from YAML configuration files.

        Args:
            config_dir: Directory containing agent YAML configs.

        Note:
            This creates agent instances from configs. LLM providers
            must be configured separately via environment variables.
        """
        from pathlib import Path

        from harness.utils.config import load_all_agent_configs

        configs = load_all_agent_configs(config_dir)
        logger.info(f"Found {len(configs)} agent configuration(s) in {config_dir}")

        # Agent instantiation happens at a higher level
        # (requires LLM provider initialization)
        for agent_id, raw_config in configs.items():
            logger.info(f"  - Loaded config for: {agent_id}")
