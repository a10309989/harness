"""BaseTool — abstract tool with JSON Schema for LLM function calling."""

import time
from abc import ABC, abstractmethod
from typing import Any

from harness.models.tool import ToolResult
from harness.models.policy import RiskLevel
from harness.models.workflow import IdempotencyMode


class BaseTool(ABC):
    """Abstract tool with JSON Schema specification for LLM function calling.

    Each tool declares its interface via JSON Schema, making it directly
    compatible with Anthropic and OpenAI function calling APIs.

    Attributes:
        name: Unique tool name.
        description: Human-readable description.
        parameters_schema: JSON Schema for parameters.
        timeout_seconds: Execution timeout.
        requires_sandbox: Whether the tool needs sandbox isolation.
    """

    name: str
    description: str
    parameters_schema: dict
    timeout_seconds: int = 30
    requires_sandbox: bool = False
    risk_level: RiskLevel = RiskLevel.MEDIUM
    risk_tags: frozenset[str] = frozenset()
    idempotency_mode: IdempotencyMode = IdempotencyMode.NON_IDEMPOTENT

    def get_function_definition(self) -> dict:
        """Return the Anthropic/OpenAI-compatible function definition.

        Returns:
            Dict with name, description, and input_schema keys.
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self._get_json_schema_properties(),
                "required": self._get_required_params(),
            },
            "x-harness-risk": {
                "level": str(self.risk_level),
                "tags": sorted(self.risk_tags),
                "idempotency_mode": str(self.idempotency_mode),
            },
        }

    def _get_required_params(self) -> list[str]:
        """Extract required parameter names from the schema."""
        required = []
        for name, schema in self.parameters_schema.items():
            if isinstance(schema, dict) and schema.get("required", False):
                required.append(name)
        return required

    def _get_json_schema_properties(self) -> dict:
        """Return provider-compatible JSON Schema properties."""
        properties = {}
        for name, schema in self.parameters_schema.items():
            if isinstance(schema, dict):
                schema = dict(schema)
                schema.pop("required", None)
            properties[name] = schema
        return properties

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with validated parameters.

        Args:
            **kwargs: Tool-specific parameters.

        Returns:
            ToolResult with success status and data or error.
        """
        ...

    async def execute_with_timing(self, **kwargs: Any) -> ToolResult:
        """Execute the tool and measure duration.

        Args:
            **kwargs: Tool-specific parameters.

        Returns:
            ToolResult with duration_ms populated.
        """
        start = time.perf_counter()
        result = await self.execute(**kwargs)
        result.duration_ms = (time.perf_counter() - start) * 1000
        return result
