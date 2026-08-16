"""A2A JSON-RPC contracts for local and remote agent interoperability."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from harness.models.common import HarnessBaseModel
from harness.models.agent import AgentCapability


class AgentCard(HarnessBaseModel):
    agent_id: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    endpoint: str | None = None
    capabilities: list[AgentCapability] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "low"
    protocols: list[str] = Field(default_factory=lambda: ["a2a-jsonrpc"])
    metadata: dict[str, Any] = Field(default_factory=dict)


class A2AInvokeParams(HarnessBaseModel):
    agent_id: str
    message: str
    session_id: str
    input_data: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    trace_id: str | None = None
    # Cross-service HITL: the caller's durable workflow id, propagated so a
    # remote suspension can cascade back to the caller (additive).
    parent_workflow_id: str | None = None


class A2AInvokeResult(HarnessBaseModel):
    agent_id: str
    session_id: str
    status: str
    message: str
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    artifact_cards: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class A2AJsonRpcRequest(HarnessBaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class A2AJsonRpcError(HarnessBaseModel):
    code: int
    message: str
    data: dict[str, Any] | None = None


class A2AJsonRpcResponse(HarnessBaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    result: dict[str, Any] | list[Any] | None = None
    error: A2AJsonRpcError | None = None
