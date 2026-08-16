"""Common agent contracts shared by every runtime.

This is the "generic layer" extracted above all agent implementations. Any
agent — regardless of whether it is built on LangGraph, AutoGen, or the
built-in ReAct/Plan engine — implements the single ``AgentRuntime`` entry
point, and ``AgentCore`` provides the shared lifecycle (audit, context,
artifact production, error normalization) on top of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from harness.models.artifact import AgentOutcome, ArtifactVersionRef
from harness.observability.context import ExecutionContext, new_id, reset_execution_context, set_execution_context


@dataclass(frozen=True)
class AgentRequest:
    """Normalized input to any agent, regardless of the underlying framework."""

    message: str
    session_id: str
    input_data: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class AgentRuntimeResult:
    """Normalized output produced by a protocol adapter's ``execute``."""

    message: str
    status: str = "completed"
    artifacts: list[ArtifactVersionRef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AgentRuntime(Protocol):
    """The single entry point every protocol adapter must implement.

    A LangGraph runtime wraps a StateGraph; an AutoGen runtime wraps a
    GroupChat; the built-in runtime wraps the existing ``BaseAgent``. All of
    them converge here so ``AgentCore`` only ever talks to one interface.
    """

    kind: str

    async def execute(self, request: AgentRequest, ctx: ExecutionContext) -> AgentRuntimeResult:
        """Run the agent once for ``request`` and return a normalized result."""
        ...


class AgentCore:
    """Common layer: lifecycle + capabilities + artifact production + audit.

    Agents are built by composing an ``AgentRuntime`` (the framework-specific
    execution) with shared capabilities and letting ``AgentCore`` own the
    cross-cutting concerns.
    """

    def __init__(
        self,
        *,
        agent_id: str,
        name: str,
        runtime: AgentRuntime,
        capabilities=None,
        description: str = "",
        audit=None,
    ) -> None:
        self.agent_id = agent_id
        self.name = name
        self.description = description
        self.runtime = runtime
        self.capabilities = capabilities
        self._audit = audit

    async def run(self, request: AgentRequest) -> AgentOutcome:
        """Execute the agent with the full shared lifecycle."""
        ctx = ExecutionContext(trace_id=request.trace_id or new_id(), span_id=new_id())
        token = set_execution_context(ctx)
        try:
            await self._record("agent.started", request, ctx)
            try:
                result = await self.runtime.execute(request, ctx)
            except Exception as exc:
                await self._record("agent.failed", request, ctx, error=exc)
                return AgentOutcome(
                    message=f"Processing error: {exc}",
                    status="failed",
                    metadata={"agent_id": self.agent_id},
                )
            await self._record("agent.completed", request, ctx, result=result)
            return AgentOutcome(
                message=result.message,
                status=result.status,
                artifacts=result.artifacts,
                metadata={"agent_id": self.agent_id, **result.metadata},
            )
        finally:
            reset_execution_context(token)

    async def _record(self, event_type: str, request: AgentRequest, ctx: ExecutionContext, *, result=None, error=None) -> None:
        if self._audit is None:
            return
        metadata = {"agent_id": self.agent_id, "session_id": request.session_id}
        await self._audit(
            event_type,
            resource_type="agent",
            resource_id=self.agent_id,
            input_data={"message": request.message, "session_id": request.session_id},
            output_data={"message": result.message} if result is not None else None,
            metadata=metadata,
        )