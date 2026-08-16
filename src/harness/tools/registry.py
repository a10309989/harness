"""ToolRegistry — declarative tool registration and discovery."""

from harness.tools.base import BaseTool


class ToolRegistry:
    """Pluggable tool registry with declarative configuration.

    Manages tool instantiation, registration, and discovery.
    Tools can be registered directly as instances or as classes
    for lazy instantiation from YAML configuration.
    """

    _tools: dict[str, BaseTool]
    _tool_classes: dict[str, type[BaseTool]]

    def __init__(self) -> None:
        self._tools = {}
        self._tool_classes = {}
        self._register_builtins()

    def register(self, tool: BaseTool) -> None:
        """Register an instantiated tool.

        Args:
            tool: A BaseTool instance.
        """
        self._tools[tool.name] = tool

    def register_class(self, name: str, tool_cls: type[BaseTool]) -> None:
        """Register a tool class for lazy instantiation.

        Args:
            name: Tool name.
            tool_cls: Tool class (not instantiated).
        """
        self._tool_classes[name] = tool_cls

    def register_from_config(self, config: dict) -> None:
        """Instantiate and register a tool from its config dict.

        Args:
            config: Dict with 'name' and optional 'config' keys.

        Raises:
            ValueError: If the tool class is not found.
        """
        tool_name = config["name"]
        tool_cls = self._tool_classes.get(tool_name)
        if tool_cls is None:
            raise ValueError(f"Unknown tool: {tool_name}. Available: {list(self._tool_classes)}")

        tool_config = config.get("config", {})
        if tool_config:
            tool = tool_cls(**tool_config)
        else:
            tool = tool_cls()
        self.register(tool)

    def get_tool(self, name: str) -> BaseTool:
        """Get a registered tool by name.

        Args:
            name: Tool name.

        Returns:
            The BaseTool instance.

        Raises:
            KeyError: If the tool is not found.
        """
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found. Available: {self.list_tools()}")
        return self._tools[name]

    def list_tools(self) -> list[str]:
        """List all registered tool names."""
        return list(self._tools.keys())

    def get_all_definitions(self) -> list[dict]:
        """Return function definitions for all registered tools.

        Returns:
            List of dicts compatible with Anthropic/OpenAI tool calling.
        """
        return [tool.get_function_definition() for tool in self._tools.values()]

    def _register_builtins(self) -> None:
        """Register built-in tool classes for lazy instantiation."""
        from harness.tools.builtin.file_ops import FileReader, FileWriter
        from harness.tools.builtin.code_executor import CodeExecutor
        from harness.tools.builtin.web_fetcher import WebFetcher
        from harness.tools.builtin.shell_executor import ShellExecutor

        self.register_class("file_reader", FileReader)
        self.register_class("file_writer", FileWriter)
        self.register_class("code_executor", CodeExecutor)
        self.register_class("web_fetcher", WebFetcher)
        self.register_class("shell_executor", ShellExecutor)
