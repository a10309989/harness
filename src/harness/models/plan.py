"""Serializable multi-agent plan contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from harness.models.common import HarnessBaseModel


class PlanNodeType(StrEnum):
    AGENT = "agent"
    TOOL = "tool"
    HUMAN = "human"
    A2A = "a2a"


class PlanNode(HarnessBaseModel):
    id: str
    node_type: PlanNodeType = PlanNodeType.AGENT
    description: str
    depends_on: list[str] = Field(default_factory=list)
    agent_id: str | None = None
    capability: str | None = None
    input_data: dict[str, Any] = Field(default_factory=dict)


class ExecutionPlan(HarnessBaseModel):
    schema_version: int = 1
    objective: str
    nodes: list[PlanNode]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_graph(self) -> "ExecutionPlan":
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("Plan node IDs must be unique")
        known = set(ids)
        for node in self.nodes:
            unknown = set(node.depends_on) - known
            if unknown:
                raise ValueError(
                    f"Plan node '{node.id}' has unknown dependencies: "
                    f"{sorted(unknown)}"
                )
            if node.id in node.depends_on:
                raise ValueError(f"Plan node '{node.id}' cannot depend on itself")
        self.topological_batches()
        return self

    def topological_batches(self) -> list[list[PlanNode]]:
        remaining = {node.id: node for node in self.nodes}
        completed: set[str] = set()
        batches: list[list[PlanNode]] = []
        while remaining:
            ready = [
                node
                for node in remaining.values()
                if set(node.depends_on).issubset(completed)
            ]
            if not ready:
                raise ValueError("Plan graph contains a dependency cycle")
            ready.sort(key=lambda item: item.id)
            batches.append(ready)
            for node in ready:
                completed.add(node.id)
                remaining.pop(node.id)
        return batches
