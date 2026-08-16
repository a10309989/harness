"""Tool management API routes."""

from fastapi import APIRouter, Depends

from harness.api.deps import get_agent_registry
from harness.core.registry import AgentRegistry
from harness.security.dependencies import require_permission

router = APIRouter(dependencies=[Depends(require_permission("agent:read"))])


@router.get("")
async def list_tools(registry: AgentRegistry = Depends(get_agent_registry)):
    """List all available tools across all agents (local + MCP)."""
    all_tools = {}
    for agent_id, agent in registry.get_all_agents().items():
        if hasattr(agent, "tool_registry") and agent.tool_registry:
            for name in agent.tool_registry.list_tools():
                try:
                    tool = agent.tool_registry.get_tool(name)
                    all_tools[name] = {
                        "name": tool.name,
                        "description": tool.description,
                        "provided_by": agent_id,
                        "risk_level": str(tool.risk_level),
                        "risk_tags": sorted(tool.risk_tags),
                    }
                except KeyError:
                    pass
    return {"tools": list(all_tools.values()), "count": len(all_tools)}
