"""MCPToolProxy — adapts MCP remote tools to the framework's Tool interface."""

from harness.models.tool import ToolDefinition, ToolResult
from harness.models.policy import RiskLevel
from harness.models.workflow import IdempotencyMode
from harness.mcp.manager import MCPManager
from harness.tools.base import BaseTool


class MCPToolProxy(BaseTool):
    """Adapts an MCP remote tool to the framework's BaseTool interface.

    When an agent's ToolRegistry contains an MCPToolProxy, the agent
    sees it as a standard tool. Execution is transparently forwarded
    to the remote MCP server via MCPManager.
    """

    def __init__(self, tool_def: ToolDefinition, mcp_manager: MCPManager, server_name: str) -> None:
        super().__init__()
        self.name = tool_def.name
        self.description = tool_def.description
        self.parameters_schema = tool_def.input_schema.get("properties", {})
        self.timeout_seconds = tool_def.timeout_seconds
        self.requires_sandbox = False  # MCP tools execute remotely
        self.risk_level = RiskLevel.HIGH
        self.risk_tags = frozenset({"mcp", "remote"})
        self.idempotency_mode = IdempotencyMode.NON_IDEMPOTENT
        self._mcp_manager = mcp_manager
        self._server_name = server_name

    async def execute(self, **kwargs) -> ToolResult:
        """Forward execution to the remote MCP server."""
        return await self._mcp_manager.call_tool(self.name, kwargs)
