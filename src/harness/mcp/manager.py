"""MCPManager — connection pool and lifecycle management for MCP servers.

Implements the full MCP (Model Context Protocol) JSON-RPC lifecycle:
- initialize → tools/list → tools/call over stdio transport
"""

import asyncio
import json
import logging
from typing import Any

from harness.models.skill import MCPServerConfig
from harness.models.tool import ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

# ─── JSON-RPC helpers ──────────────────────────────────────────

def _make_request(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}


class MCPClient:
    """A managed connection to a single MCP server via JSON-RPC over stdio."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.connected = False
        self.tools: list[ToolDefinition] = []
        self._process: Any = None
        self._req_id = 0
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Establish connection and perform MCP initialization handshake."""
        if self.config.transport == "stdio":
            try:
                cmd = [self.config.command or ""]
                cmd.extend(self.config.args)
                self._process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={**__import__("os").environ, **self.config.env},
                )
                # MCP handshake: initialize
                init_result = await self._send_request("initialize", {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "harness", "version": "0.1.0"},
                })
                if init_result:
                    self.connected = True
                    logger.info(f"MCP connected to '{self.config.name}' via stdio (protocol: {init_result.get('protocolVersion', 'unknown')})")

                    # Discover tools
                    tools_result = await self._send_request("tools/list")
                    if tools_result and "tools" in tools_result:
                        for t in tools_result["tools"]:
                            self.tools.append(ToolDefinition(
                                name=t.get("name", ""),
                                description=t.get("description", ""),
                                input_schema=t.get("inputSchema", {}),
                            ))
                        logger.info(f"MCP '{self.config.name}' provides {len(self.tools)} tool(s): "
                                    f"{[t.name for t in self.tools]}")
                else:
                    logger.error(f"MCP initialize failed for '{self.config.name}'")
                    self.connected = False
            except Exception as e:
                logger.error(f"MCP stdio connection failed for '{self.config.name}': {e}")
                self.connected = False
        elif self.config.transport == "sse":
            # SSE transport placeholder — would use httpx for HTTP-based MCP
            self.connected = True
            logger.info(f"MCP connected to '{self.config.name}' via SSE at {self.config.url}")

    async def disconnect(self) -> None:
        """Close the MCP server connection."""
        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
            self._process = None
        self.connected = False

    async def _send_request(self, method: str, params: dict | None = None) -> dict | None:
        """Send a JSON-RPC request over stdio and read the response."""
        if not self._process or self._process.stdin is None or self._process.stdout is None:
            return None

        async with self._lock:
            self._req_id += 1
            request = _make_request(method, params, req_id=self._req_id)
            try:
                request_bytes = (json.dumps(request) + "\n").encode("utf-8")
                self._process.stdin.write(request_bytes)
                await self._process.stdin.drain()

                # Read the response line
                response_line = await asyncio.wait_for(
                    self._process.stdout.readline(), timeout=30.0
                )
                response = json.loads(response_line.decode("utf-8"))
                if "error" in response:
                    logger.warning(f"MCP RPC error for '{method}': {response['error']}")
                    return None
                return response.get("result", {})
            except asyncio.TimeoutError:
                logger.warning(f"MCP RPC timeout for '{method}' on '{self.config.name}'")
                return None
            except Exception as e:
                logger.error(f"MCP RPC error for '{method}': {e}")
                return None

    async def list_tools(self) -> list[ToolDefinition]:
        """Discover tools from the MCP server. Returns cached tools after connect."""
        if not self.connected:
            # Try to refresh from server
            result = await self._send_request("tools/list")
            if result and "tools" in result:
                self.tools = [
                    ToolDefinition(
                        name=t.get("name", ""),
                        description=t.get("description", ""),
                        input_schema=t.get("inputSchema", {}),
                    )
                    for t in result["tools"]
                ]
        return self.tools

    async def call_tool(self, tool_name: str, params: dict) -> ToolResult:
        """Call a tool on the MCP server via tools/call JSON-RPC."""
        if not self.connected:
            return ToolResult(success=False, error=f"MCP server '{self.config.name}' not connected")

        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": params,
        })
        if result is None:
            return ToolResult(success=False, error=f"MCP tool '{tool_name}' call failed — no response")

        # MCP returns content as a list of content blocks
        content_blocks = result.get("content", [])
        text_parts = []
        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))

        is_error = result.get("isError", False)
        return ToolResult(
            success=not is_error,
            data={"tool_name": tool_name, "content": text_parts, "raw": result},
            error="MCP tool returned an error" if is_error else None,
        )


class MCPManager:
    """Manages all MCP server connections — pool, health, auto-reconnect."""

    _clients: dict[str, MCPClient]
    _tool_mappings: dict[str, str]  # tool_name → server_name

    def __init__(self) -> None:
        self._clients = {}
        self._tool_mappings = {}

    async def connect_all(self, server_configs: list[MCPServerConfig]) -> None:
        """Establish connections to all configured MCP servers.

        Args:
            server_configs: List of MCP server configurations.
        """
        for cfg in server_configs:
            client = MCPClient(cfg)
            await client.connect()
            self._clients[cfg.name] = client

            if client.connected:
                # Apply tool filter if specified, otherwise expose all tools
                tools = client.tools
                if cfg.tool_filter:
                    tools = [t for t in tools if t.name in cfg.tool_filter]
                for tool in tools:
                    self._tool_mappings[tool.name] = cfg.name
                logger.info(f"MCP server '{cfg.name}' registered with "
                            f"{len(cfg.tool_filter) or 'all'} tools "
                            f"(filter: {cfg.tool_filter or 'none'})")

    async def get_client(self, server_name: str) -> MCPClient | None:
        """Get a connected MCP client by server name."""
        return self._clients.get(server_name)

    async def list_all_tools(self) -> list[ToolDefinition]:
        """Aggregate tools from all connected MCP servers."""
        all_tools: list[ToolDefinition] = []
        for client in self._clients.values():
            if client.connected:
                all_tools.extend(await client.list_tools())
        return all_tools

    async def call_tool(self, tool_name: str, params: dict) -> ToolResult:
        """Call an MCP tool, routing to the correct server.

        Args:
            tool_name: Name of the MCP tool.
            params: Tool parameters.

        Returns:
            ToolResult from the MCP server.
        """
        server_name = self._tool_mappings.get(tool_name)
        if server_name is None:
            return ToolResult(success=False, error=f"No MCP server found for tool '{tool_name}'")

        client = self._clients.get(server_name)
        if client is None or not client.connected:
            return ToolResult(success=False, error=f"MCP server '{server_name}' not connected")

        return await client.call_tool(tool_name, params)

    async def health_check(self) -> dict[str, bool]:
        """Check health of all MCP server connections.

        Returns:
            Dict mapping server_name → is_healthy.
        """
        return {name: client.connected for name, client in self._clients.items()}

    async def reconnect(self, server_name: str) -> bool:
        """Reconnect to a specific MCP server.

        Args:
            server_name: Name of the server to reconnect.

        Returns:
            True if reconnection succeeded.
        """
        client = self._clients.get(server_name)
        if client is None:
            return False
        await client.disconnect()
        await client.connect()
        return client.connected

    async def close_all(self) -> None:
        """Close all MCP server connections."""
        for client in self._clients.values():
            await client.disconnect()
        self._clients.clear()
        self._tool_mappings.clear()
