"""Planner policy and deterministic compilation helpers.

DAG generation is owned by the registered remote A2A Planner agent. This
module keeps only local policy checks and compilation against runtime agents.
"""

from __future__ import annotations

import re

from harness.core.registry import AgentRegistry
from harness.models.plan import ExecutionPlan, PlanNodeType

COMPLEXITY_MARKERS = (
    "焒后",
    "接着",
    "最后",
    "完整流程",
    "端到端",
    "全流程",
    "依次",
    "and then",
    "after that",
    "end-to-end",
    "pipeline",
)

_STEP_MARKER_RE = re.compile(r"(?:^|\s)(?:\d+[.)、]|[-*])\s*")


def should_plan(message: str) -> bool:
    """Heuristic: does this message look like a multi-step request?"""
    lowered = message.lower()
    marker_hits = sum(marker in lowered for marker in COMPLEXITY_MARKERS)
    enumerated_steps = len(_STEP_MARKER_RE.findall(message))
    return marker_hits >= 1 or enumerated_steps >= 2


class PlanCompiler:
    """Validate a remote plan against registered runtime capabilities."""

    def __init__(self, registry: AgentRegistry) -> None:
        self.registry = registry

    def compile(self, plan: ExecutionPlan) -> ExecutionPlan:
        available_agents = set(self.registry.list_agents()) - {"master", "planner"}
        for node in plan.nodes:
            if node.node_type == PlanNodeType.AGENT:
                if not node.agent_id:
                    raise ValueError(f"Agent node '{node.id}' requires agent_id")
                if node.agent_id not in available_agents:
                    raise ValueError(
                        f"Agent node '{node.id}' references unavailable agent "
                        f"'{node.agent_id}'"
                    )
            elif node.node_type != PlanNodeType.HUMAN:
                raise ValueError(
                    f"Node type '{node.node_type}' is not executable in this runtime"
                )
        plan.topological_batches()
        return plan
