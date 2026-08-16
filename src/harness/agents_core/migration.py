"""Migration helper: turn an existing harness ``BaseAgent`` into an ``AgentCore``.

This is the Phase-4 pattern for moving the current ``agents/`` subclasses onto
the shared core without rewriting their logic. The agent keeps its
ReAct/Plan/Hybrid engine (via ``LocalBaseAgentRuntime``) and its mixin
capabilities (via ``CapabilityProvider``); only the outer contract changes.
"""

from __future__ import annotations

from typing import Any

from harness.agents_core.contracts import AgentCore
from harness.agents_core.runtime import LocalBaseAgentRuntime


def wrap_base_agent(agent: Any, *, audit=None) -> AgentCore:
    """Wrap a ``BaseAgent`` (or any ``process_structured``-capable agent) as AgentCore.

    The agent's own mixins already satisfy ``CapabilityProvider``, so it is
    passed through directly as the capability source.
    """
    config = getattr(agent, "config", None)
    return AgentCore(
        agent_id=getattr(agent, "agent_id", "unknown"),
        name=getattr(agent, "agent_name", None) or getattr(agent, "name", "unknown"),
        description=getattr(agent, "description", "") or getattr(config, "description", ""),
        runtime=LocalBaseAgentRuntime(agent),
        capabilities=agent,
        audit=audit,
    )