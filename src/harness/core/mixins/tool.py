"""ToolMixin — pluggable tool binding and execution."""

from abc import ABC
from typing import Any

from harness.models.agent import ToolConfig
from harness.models.tool import ToolResult


class ToolMixin(ABC):
    """Capability: tool registration, schema generation, and execution.

    Tools are the atomic actions an agent can perform — file operations,
    code execution, web requests, shell commands, etc.

    Tool call definitions are generated in a format compatible with
    both Anthropic and OpenAI function calling APIs.
    """

    tool_registry: Any  # ToolRegistry — set during _init_tools
    tool_executor: Any  # ToolExecutor — set during _init_tools
    _tool_configs: list[ToolConfig]

    def _init_tools(
        self,
        tool_configs: list[ToolConfig],
        runtime_services=None,
    ) -> None:
        """Initialize tool system from configuration.

        Args:
            tool_configs: List of tool configurations to register.
        """
        # Lazy import to avoid circular deps
        from harness.tools.registry import ToolRegistry
        from harness.tools.executor import ToolExecutor

        self._tool_configs = tool_configs
        self.tool_registry = ToolRegistry()
        self.tool_executor = ToolExecutor(
            self.tool_registry,
            runtime_services=runtime_services,
        )

        for cfg in tool_configs:
            if cfg.enabled:
                try:
                    self.tool_registry.register_from_config(cfg.model_dump())
                except Exception:
                    pass  # Tool might need manual registration

    def get_tool_definitions(self) -> list[dict]:
        """Return JSON Schema tool definitions for LLM function calling.

        Returns:
            List of tool definitions compatible with Anthropic/OpenAI APIs.
        """
        if not hasattr(self, "tool_registry") or self.tool_registry is None:
            return []
        return self.tool_registry.get_all_definitions()

    async def execute_tool(self, name: str, params: dict[str, Any]) -> ToolResult:
        """Execute a named tool with the given parameters.

        Args:
            name: Tool name to execute.
            params: Parameters to pass to the tool.

        Returns:
            ToolResult with success status, data, or error.

        Raises:
            ValueError: If the tool is not found.
        """
        if not hasattr(self, "tool_executor") or self.tool_executor is None:
            return ToolResult(success=False, error="Tool executor not initialized")
        return await self.tool_executor.execute(name, params)

    def register_tool(self, tool: Any) -> None:
        """Register a tool instance directly.

        Args:
            tool: A BaseTool instance to register.
        """
        if hasattr(self, "tool_registry") and self.tool_registry is not None:
            self.tool_registry.register(tool)
