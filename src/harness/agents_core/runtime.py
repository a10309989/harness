"""Protocol adapters: converge different agent frameworks onto AgentRuntime.

Each adapter is a thin translation layer. The framework it wraps (the existing
``BaseAgent`` engine, a LangGraph StateGraph, an AutoGen group chat, ...) stays
unchanged; only the call boundary is normalized.
"""

from __future__ import annotations

from typing import Any, Callable, Awaitable

from harness.agents_core.contracts import AgentRequest, AgentRuntime, AgentRuntimeResult
from harness.observability.context import ExecutionContext


class LocalBaseAgentRuntime:
    """Adapts an existing harness ``BaseAgent`` (ReAct/Plan/Hybrid) to AgentRuntime.

    The ``BaseAgent`` already owns the mixin capabilities and the three
    execution modes; this adapter simply exposes its ``process_structured``
    entry point through the shared contract.
    """

    kind = "react"

    def __init__(self, agent: Any) -> None:
        self._agent = agent

    async def execute(self, request: AgentRequest, ctx: ExecutionContext) -> AgentRuntimeResult:
        outcome = await self._agent.process_structured(request.message, request.session_id)
        return AgentRuntimeResult(
            message=outcome.message,
            status=outcome.status,
            artifacts=outcome.artifacts,
            metadata=outcome.metadata,
        )


class LangGraphRuntime:
    """Example adapter that runs a LangGraph StateGraph as an AgentRuntime.

    ``build_graph`` is a zero-argument callable returning a compiled graph
    whose state carries ``message`` and produces ``output``. LangGraph is
    imported lazily so the package is importable without it installed.
    """

    kind = "langgraph"

    def __init__(self, build_graph: Callable[[], Any]) -> None:
        self._build_graph = build_graph

    async def execute(self, request: AgentRequest, ctx: ExecutionContext) -> AgentRuntimeResult:
        try:
            from langgraph.graph import StateGraph  # noqa: F401  (eager import check)
        except ImportError as exc:  # pragma: no cover - langgraph is a declared dep
            raise RuntimeError("langgraph is not installed; cannot run LangGraphRuntime") from exc

        graph = self._build_graph()
        state = await graph.ainvoke({"message": request.message, "session_id": request.session_id})
        output = state.get("output") or state.get("message") or ""
        return AgentRuntimeResult(
            message=output,
            status="completed",
            metadata={"framework": "langgraph", "state_keys": sorted(state.keys())},
        )


class AutoGenRuntime:
    """Example adapter for an AutoGen group chat (structure only).

    AutoGen is a heavy optional dependency; the adapter is kept as a thin
    scaffold. Wire ``build_agent`` to construct a ``GroupChatManager`` and
    return its ``a_initiate_chat`` result.
    """

    kind = "autogen"

    def __init__(self, build_agent: Callable[[], Any]) -> None:
        self._build_agent = build_agent

    async def execute(self, request: AgentRequest, ctx: ExecutionContext) -> AgentRuntimeResult:
        agent = self._build_agent()
        result = await agent.a_initiate_chat(agent=agent, message=request.message)
        return AgentRuntimeResult(
            message=result.chat_history[-1].content if result.chat_history else "",
            status="completed",
            metadata={"framework": "autogen"},
        )