"""Reusable A2A remote agent server framework."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException

from harness.models.a2a import (
    A2AInvokeParams,
    A2AInvokeResult,
    A2AJsonRpcError,
    A2AJsonRpcRequest,
    A2AJsonRpcResponse,
    AgentCard,
)
from harness.observability.context import new_id

logger = logging.getLogger(__name__)


class RemoteA2AAgent(ABC):
    """Base class for independently deployable A2A agents."""

    card: AgentCard

    def __init__(self, card: AgentCard) -> None:
        self.card = card

    @abstractmethod
    async def invoke(self, params: A2AInvokeParams) -> A2AInvokeResult:
        """Execute one remote agent request."""

    async def status(self) -> dict[str, Any]:
        return {
            "agent_id": self.card.agent_id,
            "name": self.card.name,
            "state": "idle",
            "version": self.card.version,
        }


class RemoteAgentRegistry:
    """Small in-process registry for one remote-agent service."""

    def __init__(self) -> None:
        self._agents: dict[str, RemoteA2AAgent] = {}

    def register(self, agent: RemoteA2AAgent) -> None:
        self._agents[agent.card.agent_id] = agent

    def get(self, agent_id: str) -> RemoteA2AAgent:
        try:
            return self._agents[agent_id]
        except KeyError as exc:
            raise KeyError(f"Remote agent '{agent_id}' not found") from exc

    def cards(self) -> list[AgentCard]:
        return [agent.card for agent in self._agents.values()]


def create_a2a_agent_app(
    agents: list[RemoteA2AAgent],
    *,
    service_name: str = "Harness Remote A2A Agents",
    auth_token: str = "",
) -> FastAPI:
    """Create a deployable FastAPI app that exposes A2A JSON-RPC."""

    registry = RemoteAgentRegistry()
    for agent in agents:
        registry.register(agent)

    app = FastAPI(title=service_name)
    app.state.agent_registry = registry

    async def require_token(authorization: str = Header(default="")) -> None:
        if not auth_token:
            return
        expected = f"Bearer {auth_token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="Invalid A2A token")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "agents": len(registry.cards())}

    @app.get("/.well-known/agent-cards")
    async def agent_cards(_: None = Depends(require_token)) -> dict[str, Any]:
        return {"agents": [card.model_dump(mode="json") for card in registry.cards()]}

    @app.post("/a2a")
    async def jsonrpc(
        request: A2AJsonRpcRequest,
        _: None = Depends(require_token),
    ) -> A2AJsonRpcResponse:
        try:
            result = await dispatch_a2a_request(request.method, request.params, registry)
            return A2AJsonRpcResponse(id=request.id, result=result)
        except KeyError as exc:
            return _error(request.id, -32004, str(exc))
        except ValueError as exc:
            return _error(request.id, -32602, str(exc))
        except Exception as exc:
            logger.exception("Remote A2A request failed")
            return _error(
                request.id,
                -32000,
                "Remote agent execution failed",
                {"type": exc.__class__.__name__, "message": str(exc)},
            )

    return app


async def dispatch_a2a_request(
    method: str,
    params: Mapping[str, Any],
    registry: RemoteAgentRegistry,
) -> dict[str, Any] | list[Any]:
    if method == "agent.discover":
        agent_id = params.get("agent_id")
        if agent_id:
            return registry.get(str(agent_id)).card.model_dump(mode="json")
        return [card.model_dump(mode="json") for card in registry.cards()]
    if method == "agent.invoke":
        invoke = A2AInvokeParams.model_validate(params)
        agent = registry.get(invoke.agent_id)
        result = await agent.invoke(invoke)
        return result.model_dump(mode="json")
    if method == "agent.status":
        agent_id = params.get("agent_id")
        if not agent_id:
            raise ValueError("agent_id is required")
        return await registry.get(str(agent_id)).status()
    raise ValueError(f"Unsupported A2A method: {method}")


def completed_result(
    params: A2AInvokeParams,
    message: str,
    *,
    metadata: dict[str, Any] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
) -> A2AInvokeResult:
    return A2AInvokeResult(
        agent_id=params.agent_id,
        session_id=params.session_id,
        status="completed",
        message=message,
        artifacts=artifacts or [],
        metadata={
            "request_id": new_id(),
            **(metadata or {}),
        },
    )


def _error(
    request_id: str | int | None,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> A2AJsonRpcResponse:
    return A2AJsonRpcResponse(
        id=request_id,
        error=A2AJsonRpcError(code=code, message=message, data=data),
    )
