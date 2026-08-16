"""Agent management API routes — with MCP binding support."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from harness.api.config_store import ConfigStore
from harness.api.deps import get_agent_registry, get_config_store, get_database
from harness.api.wire_agents import create_llm_router
from harness.db.protocols import DatabaseProtocol
from harness.observability.audit import record_audit
from harness.security.dependencies import require_permission

router = APIRouter(dependencies=[Depends(require_permission("agent:read"))])


def _mcp_server_name(server) -> str:
    if isinstance(server, dict):
        return str(server.get("name", ""))
    return str(getattr(server, "name", ""))


def _mcp_server_summary(server) -> dict:
    if isinstance(server, dict):
        return {
            "name": server.get("name", ""),
            "transport": server.get("transport", ""),
        }
    return {
        "name": getattr(server, "name", ""),
        "transport": getattr(server, "transport", ""),
    }


def _agent_summary(agent) -> dict:
    config = getattr(agent, "config", None)
    card = getattr(agent, "card", None)
    metadata = getattr(card, "metadata", {}) if card is not None else {}
    mcp_servers = getattr(config, "mcp_servers", []) if config is not None else []
    execution_mode = getattr(config, "execution_mode", None)
    if execution_mode is None:
        execution_mode = metadata.get("execution_mode", "orchestrator" if card is None else "remote")
    return {
        "id": getattr(agent, "agent_id", getattr(card, "agent_id", "")),
        "name": getattr(agent, "agent_name", getattr(card, "name", "")),
        "state": getattr(agent, "state", "unknown"),
        "execution_mode": execution_mode,
        "bind_skills": getattr(config, "bind_skills", []),
        "mcp_servers": [_mcp_server_name(server) for server in mcp_servers],
        "remote": config is None and card is not None,
        "protocols": getattr(card, "protocols", []) if card is not None else ["local"],
        # Agent Hub heartbeat snapshot
        "heartbeat_status": getattr(agent, "heartbeat_status", "unknown"),
        "last_heartbeat": getattr(agent, "last_heartbeat", None),
        "reported_capabilities": getattr(agent, "reported_capabilities", []),
        "reported_skills": getattr(agent, "reported_skills", []),
        "reported_runtime": getattr(agent, "reported_runtime", ""),
        "reported_mcp": getattr(agent, "reported_mcp", []),
        "reported_tools": getattr(agent, "reported_tools", []),
        "reported_prompts": getattr(agent, "reported_prompts", []),
        "reported_knowledge_bases": getattr(agent, "reported_knowledge_bases", []),
    }


@router.get("")
async def list_agents(registry=Depends(get_agent_registry)):
    """List all registered agents with their MCP bindings."""
    agents_list = []
    for agent_id, agent in registry.get_all_agents().items():
        agents_list.append(_agent_summary(agent))
    return {"agents": agents_list, "count": len(agents_list)}


# ─── Remote A2A Services (Agent Hub 管理面) ───────────────────────

class RemoteServiceRequest(BaseModel):
    endpoint: str
    token: str = ""
    timeout_seconds: int = 300
    skip_local: bool = True


class RemoteServiceOut(BaseModel):
    id: str
    endpoint: str
    token: str = ""
    timeout_seconds: int = 300
    skip_local: bool = True
    enabled: bool = True


def _row_to_service(row) -> dict:
    return {
        "id": row["id"],
        "endpoint": row["endpoint"],
        "token": row.get("token") or "",
        "timeout_seconds": row.get("timeout_seconds", 300),
        "skip_local": bool(row.get("skip_local", 1)),
        "enabled": bool(row.get("enabled", 1)),
    }


@router.get(
    "/remote-services",
    dependencies=[Depends(require_permission("agent:read"))],
)
async def list_remote_services(db: DatabaseProtocol = Depends(get_database)):
    rows = await db.fetch_all(
        "SELECT id, endpoint, token, timeout_seconds, skip_local, enabled "
        "FROM remote_services ORDER BY created_at"
    )
    return {"services": [_row_to_service(r) for r in rows], "count": len(rows)}


@router.post(
    "/remote-services",
    dependencies=[Depends(require_permission("agent:manage"))],
)
async def add_remote_service(
    body: RemoteServiceRequest,
    request: Request,
    db: DatabaseProtocol = Depends(get_database),
    registry=Depends(get_agent_registry),
):
    """Import a remote A2A service: fetch its agent cards and register them."""
    from datetime import datetime, timezone
    from harness.a2a.importer import import_remote_agents_from_endpoint

    endpoint = body.endpoint.strip().rstrip("/")
    if not endpoint.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="endpoint must be http(s)://")

    # Idempotency: same endpoint already registered?
    existing = await db.fetch_one(
        "SELECT id FROM remote_services WHERE endpoint = $1", (endpoint,)
    )
    service_id = existing["id"] if existing else None

    # Import remote agents (skip locally-owned orchestration agents).
    skip_ids = {"master", "planner"} if body.skip_local else set()
    try:
        imported = await import_remote_agents_from_endpoint(
            endpoint=endpoint,
            registry=registry,
            token=body.token,
            timeout_seconds=body.timeout_seconds,
            skip_agent_ids=skip_ids,
            artifact_service=request.app.state.artifact_service,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Import failed: {exc}") from exc

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if existing:
        await db.execute(
            "UPDATE remote_services SET token=$1, timeout_seconds=$2, skip_local=$3, "
            "enabled=1, updated_at=$4 WHERE id=$5",
            (body.token, body.timeout_seconds, int(body.skip_local), now, service_id),
        )
    else:
        from harness.utils.id_gen import generate_id

        service_id = generate_id()
        await db.execute(
            "INSERT INTO remote_services (id, endpoint, token, timeout_seconds, skip_local, enabled, created_at, updated_at) "
            "VALUES ($1, $2, $3, $4, $5, 1, $6, $6)",
            (service_id, endpoint, body.token, body.timeout_seconds, int(body.skip_local), now),
        )
    await db.commit()
    await record_audit(
        "config.remote_service.added",
        resource_type="remote_service",
        resource_id=service_id,
        input_data={"endpoint": endpoint, "imported": imported},
        metadata={"imported_agents": len(imported)},
    )
    return {
        "id": service_id,
        "endpoint": endpoint,
        "imported": imported,
        "imported_count": len(imported),
    }


@router.delete(
    "/remote-services/{service_id}",
    dependencies=[Depends(require_permission("agent:manage"))],
)
async def remove_remote_service(
    service_id: str,
    db: DatabaseProtocol = Depends(get_database),
    registry=Depends(get_agent_registry),
):
    """Remove a remote service and cascade-unregister its imported agents."""
    row = await db.fetch_one(
        "SELECT id, endpoint FROM remote_services WHERE id = $1", (service_id,)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Remote service not found")

    endpoint = row["endpoint"]
    # Cascade-unregister agents imported from this endpoint (remote adapters).
    removed = []
    for agent_id, agent in list(registry.get_all_agents().items()):
        if agent_id in {"master", "planner"}:
            continue
        agent_endpoint = getattr(agent, "endpoint", None)
        if agent_endpoint and str(agent_endpoint).rstrip("/") == endpoint:
            registry.unregister(agent_id)
            removed.append(agent_id)
    await db.execute("DELETE FROM remote_services WHERE id = $1", (service_id,))
    await db.commit()
    await record_audit(
        "config.remote_service.removed",
        resource_type="remote_service",
        resource_id=service_id,
        metadata={"endpoint": endpoint, "unregistered": removed},
    )
    return {"removed": True, "endpoint": endpoint, "unregistered_agents": removed}


@router.get("/{agent_id}")
async def get_agent(agent_id: str, registry=Depends(get_agent_registry)):
    """Get detailed configuration for a specific agent."""
    try:
        agent = registry.get_agent(agent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    config = getattr(agent, "config", None)
    if config is None:
        card = getattr(agent, "card", None)
        if card is None:
            return {
                "id": getattr(agent, "agent_id", agent_id),
                "name": getattr(agent, "agent_name", agent_id),
                "description": getattr(agent, "__doc__", "") or "Built-in local orchestrator agent.",
                "execution_mode": "orchestrator",
                "react_max_iterations": None,
                "llm": None,
                "bind_skills": [],
                "mcp_servers": [],
                "tools": [],
                "knowledge_bases": [],
                "vector_collection": None,
                "state": getattr(agent, "state", "unknown"),
                "remote": False,
                "endpoint": None,
                "capabilities": [],
                "protocols": ["local"],
                "risk_level": "low",
                "metadata": {"configless": True, "kind": "local_orchestrator"},
            }
        return {
            "id": card.agent_id,
            "name": card.name,
            "description": card.description,
            "execution_mode": card.metadata.get("execution_mode", "remote"),
            "react_max_iterations": None,
            "llm": None,
            "bind_skills": [],
            "mcp_servers": [],
            "tools": [],
            "knowledge_bases": [],
            "vector_collection": None,
            "state": getattr(agent, "state", "unknown"),
            "remote": True,
            "endpoint": getattr(agent, "endpoint", card.endpoint),
            "capabilities": [capability.model_dump(mode="json") for capability in card.capabilities],
            "protocols": card.protocols,
            "risk_level": card.risk_level,
            "metadata": card.metadata,
            "version": card.version,
            # Heartbeat snapshot (reported by the remote service's /agent-status)
            "heartbeat_status": getattr(agent, "heartbeat_status", "unknown"),
            "last_heartbeat": getattr(agent, "last_heartbeat", None),
            "reported_capabilities": getattr(agent, "reported_capabilities", []),
            "reported_skills": getattr(agent, "reported_skills", []),
            "reported_tools": getattr(agent, "reported_tools", []),
            "reported_mcp": getattr(agent, "reported_mcp", []),
            "reported_runtime": getattr(agent, "reported_runtime", ""),
        "reported_prompts": getattr(agent, "reported_prompts", []),
        "reported_knowledge_bases": getattr(agent, "reported_knowledge_bases", []),
        }

    return {
        "id": config.id,
        "name": config.name,
        "description": config.description,
        "execution_mode": config.execution_mode,
        "react_max_iterations": config.react_max_iterations,
        "llm": {
            "provider": config.llm.provider,
            "model": config.llm.model,
            "temperature": config.llm.temperature,
            "max_tokens": config.llm.max_tokens,
        },
        "effective_llm": getattr(getattr(agent, "llm_router", None), "effective_config", None),
        "bind_skills": config.bind_skills,
        "mcp_servers": [_mcp_server_summary(server) for server in config.mcp_servers],
        "tools": [t.name for t in config.tools if t.enabled],
        "knowledge_bases": [kb.name for kb in config.knowledge_bases],
        "prompts": list((config.prompts or {}).keys()),
        "vector_collection": config.vector.collection_name,
        "state": agent.state,
        "remote": False,
    }


# ─── Agent-MCP Binding ─────────────────────────────────────────

@router.get("/{agent_id}/mcp")
async def get_agent_mcp_bindings(agent_id: str, store: ConfigStore = Depends(get_config_store), registry=Depends(get_agent_registry)):
    """Get MCP server bindings for a specific agent, including per-agent tool_filter."""
    try:
        agent = registry.get_agent(agent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    # Get saved per-agent bindings
    bindings = await store.get_agent_mcp_bindings(agent_id)
    # Get all available MCP servers
    all_servers = {s["name"]: s for s in await store.get_mcp_servers()}

    result = []
    for server_name, server_cfg in all_servers.items():
        agent_binding = bindings.get(server_name, {})
        result.append({
            "server_name": server_name,
            "transport": server_cfg.get("transport", "stdio"),
            "enabled": agent_binding.get("enabled", False),
            "tool_filter": agent_binding.get("tool_filter", server_cfg.get("tool_filter", [])),
            "all_available_tools": server_cfg.get("tool_filter", []),
        })

    return {"agent_id": agent_id, "mcp_bindings": result, "count": len(result)}


@router.put(
    "/{agent_id}/mcp",
    dependencies=[Depends(require_permission("mcp:manage"))],
)
async def update_agent_mcp_bindings(agent_id: str, body: dict, store: ConfigStore = Depends(get_config_store), registry=Depends(get_agent_registry)):
    """Update MCP bindings for an agent.

    Body: {"bindings": {"server_name": {"enabled": true, "tool_filter": ["tool_a"]}, ...}}
    """
    try:
        agent = registry.get_agent(agent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    bindings = body.get("bindings", {})
    await store.set_agent_mcp_bindings(agent_id, bindings)

    # Update agent's MCP config dynamically
    mcp_configs = []
    all_servers = {s["name"]: s for s in await store.get_mcp_servers()}
    for server_name, binding in bindings.items():
        if binding.get("enabled", False) and server_name in all_servers:
            server_cfg = all_servers[server_name]
            from harness.models.skill import MCPServerConfig, MCPTransportType
            mcp_configs.append(MCPServerConfig(
                name=server_name,
                transport=MCPTransportType(server_cfg.get("transport", "stdio")),
                command=server_cfg.get("command"),
                args=server_cfg.get("args", []),
                env=server_cfg.get("env", {}),
                url=server_cfg.get("url"),
                headers=server_cfg.get("headers", {}),
                tool_filter=binding.get("tool_filter", server_cfg.get("tool_filter", [])),
                auto_reconnect=server_cfg.get("auto_reconnect", True),
            ))

    agent.config.mcp_servers = mcp_configs
    await record_audit(
        "config.agent_mcp.updated",
        resource_type="agent",
        resource_id=agent_id,
        input_data=bindings,
        metadata={"mcp_count": len(mcp_configs)},
    )
    return {
        "message": f"Updated MCP bindings for agent '{agent_id}'",
        "agent_id": agent_id,
        "mcp_count": len(mcp_configs),
    }


# ─── Agent LLM Override ────────────────────────────────────────

@router.put(
    "/{agent_id}/llm",
    dependencies=[Depends(require_permission("config:write"))],
)
async def update_agent_llm(agent_id: str, body: dict, store: ConfigStore = Depends(get_config_store), registry=Depends(get_agent_registry)):
    """Override LLM provider for a specific agent.

    Body: {provider: "openai", model: "gpt-4o", api_key_name: "my-openai-key"}
    The api_key_name references a saved LLM provider config.
    """
    try:
        agent = registry.get_agent(agent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    provider_name = body.get("provider", "")
    model = body.get("model", "")
    api_key_name = body.get("api_key_name", "")

    if provider_name:
        agent.config.llm.provider = provider_name
    if model:
        agent.config.llm.model = model

    await store.set("agent_llm_overrides", agent_id, {
        "provider": provider_name,
        "model": model,
        "api_key_name": api_key_name,
    })
    agent.llm_router = await create_llm_router(agent.config.llm, agent_id=agent_id, config_store=store)
    effective_llm = getattr(agent.llm_router, "effective_config", None)
    await record_audit(
        "config.agent_llm.updated",
        resource_type="agent",
        resource_id=agent_id,
        input_data={
            "provider": provider_name,
            "model": model,
            "api_key_name": api_key_name,
        },
        metadata={"provider": agent.config.llm.provider, "model": agent.config.llm.model},
    )

    return {
        "message": f"LLM config updated for agent '{agent_id}'",
        "agent_id": agent_id,
        "provider": agent.config.llm.provider,
        "model": agent.config.llm.model,
        "effective_llm": effective_llm,
    }


