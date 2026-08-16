"""A2A JSON-RPC gateway routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from harness.a2a.importer import import_remote_agents_from_endpoint
from harness.api.deps import get_agent_registry
from harness.api.deps import get_artifact_service
from harness.a2a.strategy_artifacts import generate_qa_agent_strategy_artifact
from harness.artifacts.service import ArtifactService
from harness.core.registry import AgentRegistry
from harness.models.a2a import (
    A2AInvokeParams,
    A2AInvokeResult,
    A2AJsonRpcError,
    A2AJsonRpcRequest,
    A2AJsonRpcResponse,
    AgentCard,
)
from harness.observability.audit import record_audit
from harness.security.dependencies import require_permission

router = APIRouter(dependencies=[Depends(require_permission("a2a:invoke"))])


@router.get("/cards")
async def list_agent_cards(registry: AgentRegistry = Depends(get_agent_registry)):
    return {
        "agents": [_agent_card(agent).model_dump(mode="json") for agent in registry.get_all_agents().values()]
    }


@router.post("")
async def jsonrpc(
    request: A2AJsonRpcRequest,
    registry: AgentRegistry = Depends(get_agent_registry),
) -> A2AJsonRpcResponse:
    try:
        result = await _dispatch(request.method, request.params, registry)
        return A2AJsonRpcResponse(id=request.id, result=result)
    except KeyError as exc:
        return _error(request.id, -32004, str(exc))
    except ValueError as exc:
        return _error(request.id, -32602, str(exc))
    except Exception as exc:
        return _error(request.id, -32000, str(exc), {"type": exc.__class__.__name__})


@router.post(
    "/requirements/qa-agent-strategy-artifact",
    dependencies=[Depends(require_permission("artifact:write"))],
)
async def create_qa_agent_strategy_artifact(
    artifact_service: ArtifactService = Depends(get_artifact_service),
):
    ref = await generate_qa_agent_strategy_artifact(artifact_service)
    return {"artifact": ref.model_dump(mode="json")}


@router.post(
    "/remotes/import",
    dependencies=[Depends(require_permission("agent:invoke"))],
)
async def import_remote_agents(
    body: dict,
    registry: AgentRegistry = Depends(get_agent_registry),
):
    endpoint = str(body.get("endpoint", "")).rstrip("/")
    token = str(body.get("token", ""))
    timeout_seconds = float(body.get("timeout_seconds", 30))
    if not endpoint:
        return _error(None, -32602, "endpoint is required")
    imported = await import_remote_agents_from_endpoint(
            endpoint=endpoint,
            registry=registry,
            token=token,
            timeout_seconds=timeout_seconds,
    )
    await record_audit(
        "a2a.remote_agents.imported",
        resource_type="a2a_remote",
        resource_id=endpoint,
        output_data={"agent_ids": imported},
        metadata={"count": len(imported)},
    )
    return {"endpoint": endpoint, "imported": imported, "count": len(imported)}


async def _dispatch(
    method: str,
    params: dict[str, Any],
    registry: AgentRegistry,
) -> dict[str, Any] | list[Any]:
    if method == "agent.discover":
        agent_id = params.get("agent_id")
        if agent_id:
            return _agent_card(registry.get_agent(agent_id)).model_dump(mode="json")
        return [_agent_card(agent).model_dump(mode="json") for agent in registry.get_all_agents().values()]
    if method == "agent.invoke":
        invoke = A2AInvokeParams.model_validate(params)
        agent = registry.get_agent(invoke.agent_id)
        outcome = await agent.process_structured(invoke.message, invoke.session_id)
        await record_audit(
            "a2a.agent.invoked",
            resource_type="agent",
            resource_id=invoke.agent_id,
            input_data={
                "session_id": invoke.session_id,
                "idempotency_key": invoke.idempotency_key,
            },
            output_data={"status": outcome.status},
        )
        return A2AInvokeResult(
            agent_id=invoke.agent_id,
            session_id=invoke.session_id,
            status=outcome.status,
            message=outcome.message,
            artifacts=[artifact.model_dump(mode="json") for artifact in outcome.artifacts],
            metadata=outcome.metadata,
        ).model_dump(mode="json")
    if method == "agent.status":
        agent_id = params.get("agent_id")
        if not agent_id:
            raise ValueError("agent_id is required")
        agent = registry.get_agent(agent_id)
        return {
            "agent_id": agent.agent_id,
            "name": agent.agent_name,
            "state": str(agent.state),
        }
    raise ValueError(f"Unsupported A2A method: {method}")


def _agent_card(agent) -> AgentCard:
    config = getattr(agent, "config", None)
    card = getattr(agent, "card", None)
    if card is not None:
        return card
    return AgentCard(
        agent_id=agent.agent_id,
        name=agent.agent_name,
        description=getattr(config, "description", "") if config is not None else "",
        capabilities=getattr(config, "capabilities", []) if config is not None else [],
        input_schema=_merged_schema(config, "input_schema") if config is not None else {},
        output_schema=_merged_schema(config, "output_schema") if config is not None else {},
        metadata={
            "execution_mode": str(getattr(config, "execution_mode", "")) if config is not None else "",
            "local": True,
        },
    )


def _merged_schema(config, field_name: str) -> dict[str, Any]:
    for capability in getattr(config, "capabilities", []):
        schema = getattr(capability, field_name, None)
        if schema:
            return schema
    return {}


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
