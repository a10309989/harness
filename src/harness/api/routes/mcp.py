"""MCP management API routes — CRUD + connect/disconnect + per-agent binding."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from harness.api.config_store import ConfigStore
from harness.api.deps import get_config_store, get_mcp_manager
from harness.mcp.manager import MCPManager
from harness.observability.audit import record_audit
from harness.security.dependencies import require_permission

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_permission("mcp:manage"))])


# ─── MCP Server CRUD ───────────────────────────────────────────

@router.get("/servers")
async def list_mcp_servers(store: ConfigStore = Depends(get_config_store), mgr: MCPManager = Depends(get_mcp_manager)):
    """List all configured MCP servers with connection status."""
    servers = await store.get_mcp_servers()
    health = await mgr.health_check() if mgr else {}

    result = []
    for s in servers:
        name = s.get("name", "")
        result.append({
            "name": name,
            "transport": s.get("transport", "stdio"),
            "command": s.get("command", ""),
            "url": s.get("url", ""),
            "args": s.get("args", []),
            "env": s.get("env", {}),
            "tool_filter": s.get("tool_filter", []),
            "auto_reconnect": s.get("auto_reconnect", True),
            "connected": health.get(name, False),
        })
    return {"servers": result, "count": len(result), "health": health}


@router.post("/servers")
async def create_mcp_server(body: dict, store: ConfigStore = Depends(get_config_store)):
    """Create a new MCP server configuration.

    Body: {name, transport, command?, args?, env?, url?, headers?, tool_filter?, auto_reconnect?}
    """
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Server 'name' is required")

    config = {
        "name": name,
        "transport": body.get("transport", "stdio"),
        "command": body.get("command", ""),
        "args": body.get("args", []),
        "env": body.get("env", {}),
        "url": body.get("url", ""),
        "headers": body.get("headers", {}),
        "tool_filter": body.get("tool_filter", []),
        "auto_reconnect": body.get("auto_reconnect", True),
    }
    await store.save_mcp_server(name, config)
    await record_audit(
        "config.mcp.created",
        resource_type="mcp_server",
        resource_id=name,
        input_data=config,
    )
    logger.info(f"MCP server '{name}' saved")
    return {"message": f"MCP server '{name}' created", "name": name}


@router.put("/servers/{name}")
async def update_mcp_server(name: str, body: dict, store: ConfigStore = Depends(get_config_store)):
    """Update an existing MCP server configuration."""
    existing = await store.get_mcp_server(name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")

    existing.update({
        "transport": body.get("transport", existing.get("transport", "stdio")),
        "command": body.get("command", existing.get("command", "")),
        "args": body.get("args", existing.get("args", [])),
        "env": body.get("env", existing.get("env", {})),
        "url": body.get("url", existing.get("url", "")),
        "headers": body.get("headers", existing.get("headers", {})),
        "tool_filter": body.get("tool_filter", existing.get("tool_filter", [])),
        "auto_reconnect": body.get("auto_reconnect", existing.get("auto_reconnect", True)),
    })
    await store.save_mcp_server(name, existing)
    await record_audit(
        "config.mcp.updated",
        resource_type="mcp_server",
        resource_id=name,
        input_data=existing,
    )
    return {"message": f"MCP server '{name}' updated"}


@router.delete("/servers/{name}")
async def delete_mcp_server(name: str, store: ConfigStore = Depends(get_config_store), mgr: MCPManager = Depends(get_mcp_manager)):
    """Delete an MCP server configuration and disconnect."""
    previous = await store.get_mcp_server(name)
    if await store.delete_mcp_server(name):
        # Also disconnect if connected
        if mgr:
            try:
                client = await mgr.get_client(name)
                if client:
                    await client.disconnect()
            except Exception:
                pass
        await record_audit(
            "config.mcp.deleted",
            resource_type="mcp_server",
            resource_id=name,
            input_data=previous,
        )
        return {"message": f"MCP server '{name}' deleted"}
    raise HTTPException(status_code=404, detail=f"Server '{name}' not found")


@router.get("/servers/{name}/status")
async def get_mcp_server_status(name: str, store: ConfigStore = Depends(get_config_store), mgr: MCPManager = Depends(get_mcp_manager)):
    """Get detailed status for a specific MCP server."""
    config = await store.get_mcp_server(name)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")

    connected = False
    tools: list[str] = []
    if mgr:
        client = await mgr.get_client(name)
        if client:
            connected = client.connected
            tools = [t.name for t in client.tools]

    return {
        "name": name,
        "config": config,
        "connected": connected,
        "tools": tools,
    }


@router.post("/servers/{name}/connect")
async def connect_mcp_server(name: str, store: ConfigStore = Depends(get_config_store), mgr: MCPManager = Depends(get_mcp_manager)):
    """Connect to an MCP server and discover its tools."""
    config = await store.get_mcp_server(name)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")

    from harness.models.skill import MCPServerConfig, MCPTransportType

    try:
        mcp_config = MCPServerConfig(
            name=name,
            transport=MCPTransportType(config.get("transport", "stdio")),
            command=config.get("command"),
            args=config.get("args", []),
            env=config.get("env", {}),
            url=config.get("url"),
            headers=config.get("headers", {}),
            tool_filter=config.get("tool_filter", []),
            auto_reconnect=config.get("auto_reconnect", True),
        )
        if mgr:
            await mgr.connect_all([mcp_config])
            client = await mgr.get_client(name)
            if client and client.connected:
                tools = [t.name for t in client.tools]
                await record_audit(
                    "config.mcp.connected",
                    resource_type="mcp_server",
                    resource_id=name,
                    decision="success",
                    metadata={"tools": tools},
                )
                return {"message": f"Connected to '{name}'", "connected": True, "tools": tools}
        return {"message": f"Connection attempted for '{name}'", "connected": False, "tools": []}
    except Exception as e:
        logger.error(f"Failed to connect MCP server '{name}': {e}")
        await record_audit(
            "config.mcp.connected",
            resource_type="mcp_server",
            resource_id=name,
            decision="failure",
            reason=e.__class__.__name__,
        )
        return {"message": f"Connection failed: {e}", "connected": False, "tools": []}


@router.post("/servers/{name}/reconnect")
async def reconnect_mcp_server(name: str, mgr: MCPManager = Depends(get_mcp_manager)):
    """Reconnect to an MCP server."""
    if mgr:
        success = await mgr.reconnect(name)
        return {"name": name, "reconnected": success}
    return {"name": name, "reconnected": False}


@router.post("/servers/{name}/disconnect")
async def disconnect_mcp_server(name: str, mgr: MCPManager = Depends(get_mcp_manager)):
    """Disconnect from an MCP server."""
    if mgr:
        client = await mgr.get_client(name)
        if client:
            await client.disconnect()
            return {"name": name, "disconnected": True}
    return {"name": name, "disconnected": False}
