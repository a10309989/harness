"""Common capability surface shared by all agents regardless of runtime.

The existing harness ``BaseAgent`` composes six mixins (prompt, context,
memory, vector, knowledge, tool). Those mixins *structurally* satisfy this
``CapabilityProvider`` protocol, so an existing agent can be handed straight
to ``AgentCore`` as its capability source without copying logic.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CapabilityProvider(Protocol):
    """The capability surface an agent exposes to its runtime."""

    def recall(self, key: str, default: Any = None) -> Any:
        """Short-term memory lookup."""
        ...

    def remember(self, key: str, value: Any) -> None:
        """Short-term memory write."""
        ...

    async def query_knowledge(self, kb_name: str, query: str, top_k: int = 5) -> list[dict]:
        """Search a named knowledge base."""
        ...

    async def execute_tool(self, name: str, params: dict[str, Any]) -> Any:
        """Invoke a registered tool."""
        ...

    async def semantic_search(self, query: str, top_k: int = 5, metadata_filter: dict | None = None) -> list[dict]:
        """Vector similarity search over an embedded collection."""
        ...


class NullCapabilities:
    """Empty capability provider for agents that need no shared capabilities."""

    def recall(self, key: str, default: Any = None) -> Any:
        return default

    def remember(self, key: str, value: Any) -> None:
        return None

    async def query_knowledge(self, kb_name: str, query: str, top_k: int = 5) -> list[dict]:
        return []

    async def execute_tool(self, name: str, params: dict[str, Any]) -> Any:
        return {"error": f"no capabilities available: {name}"}

    async def semantic_search(self, query: str, top_k: int = 5, metadata_filter: dict | None = None) -> list[dict]:
        return []