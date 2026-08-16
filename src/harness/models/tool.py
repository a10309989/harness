"""Tool models."""

from harness.models.common import HarnessBaseModel
from harness.models.policy import PolicyEffect, RiskLevel


class ToolParameter(HarnessBaseModel):
    """A parameter definition for a tool."""

    name: str
    type: str = "string"
    description: str = ""
    required: bool = False
    enum: list[str] | None = None
    default: str | None = None


class ToolDefinition(HarnessBaseModel):
    """Full tool definition including JSON Schema for LLM function calling."""

    name: str
    description: str
    input_schema: dict = {}
    timeout_seconds: int = 30
    requires_sandbox: bool = False
    risk_level: RiskLevel = RiskLevel.MEDIUM


class ToolResult(HarnessBaseModel):
    """Result of a tool execution."""

    success: bool
    data: dict | None = None
    error: str | None = None
    duration_ms: float | None = None
    policy_decision_id: str | None = None
    policy_decision: PolicyEffect | None = None
