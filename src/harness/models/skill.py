"""Skill and MCP configuration models."""

from enum import StrEnum

from harness.models.common import HarnessBaseModel
from harness.models.agent import KnowledgeBaseConfig, ToolConfig, VectorCollectionConfig


class SkillConfig(HarnessBaseModel):
    """Metadata definition for a skill."""

    name: str
    version: str = "1.0.0"
    description: str = ""
    category: str = "general"
    target_agents: list[str] = []
    prompt_contributions: dict[str, str] = {}
    tools: list[ToolConfig] = []
    knowledge_bases: list[KnowledgeBaseConfig] = []
    vector_collections: list[VectorCollectionConfig] = []


class MCPTransportType(StrEnum):
    STDIO = "stdio"
    SSE = "sse"


class MCPServerConfig(HarnessBaseModel):
    """Configuration for an MCP server connection."""

    name: str
    transport: MCPTransportType = MCPTransportType.STDIO
    # stdio transport
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    # SSE transport
    url: str | None = None
    headers: dict[str, str] = {}
    # Common
    auto_reconnect: bool = True
    reconnect_max_retries: int = 3
    tool_filter: list[str] = []  # empty = use all tools from this server
