"""DAG execution machinery owned by the master agent.

The master drives pipeline execution: it decides when a request needs planning
(``should_plan``), gets the DAG from the planner, then executes it here. This
module is deliberately planner-free — the planner only *generates* plans, it
never executes them.

Execution is durable: when a node suspends for human approval, the plan + every
completed node output is checkpointed into the current workflow so an approved
resume continues the remaining DAG instead of losing it.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from harness.core.registry import AgentRegistry
from harness.models.plan import ExecutionPlan, PlanNode, PlanNodeType
from harness.models.workflow import ExecutionSuspended, SuspensionInfo
from harness.observability.context import (
    get_execution_context,
    reset_execution_context,
    set_execution_context,
)


@dataclass(slots=True)
class PlanExecutionResult:
    plan: ExecutionPlan
    node_outputs: dict[str, str]

    @property
    def final_output(self) -> str:
        if not self.plan.nodes:
            return ""
        terminal_ids = {
            node.id for node in self.plan.nodes
        } - {
            dependency
            for node in self.plan.nodes
            for dependency in node.depends_on
        }
        return "\n\n".join(
            self.node_outputs[node_id]
            for node_id in sorted(terminal_ids)
            if node_id in self.node_outputs
        )


class PlanExecutor:
    """Executes validated Agent nodes in dependency-safe batches.

    ``workflow_service`` enables durable suspension: when a node's tool call
    pauses for human approval, the plan state is checkpointed into the current
    workflow before re-raising ``ExecutionSuspended``. A later resume re-drives
    the executor with ``resume_outputs`` (the already-completed node outputs).
    """

    def __init__(
        self,
        registry: AgentRegistry,
        *,
        workflow_service=None,
    ) -> None:
        self.registry = registry
        self.workflow_service = workflow_service
        self._agent_locks: dict[str, asyncio.Lock] = {}

    async def execute(
        self,
        plan: ExecutionPlan,
        *,
        session_id: str,
        resume_outputs: dict[str, str] | None = None,
    ) -> PlanExecutionResult:
        outputs = dict(resume_outputs or {})
        for batch in plan.topological_batches():
            pending = [node for node in batch if node.id not in outputs]
            if not pending:
                continue
            results = await asyncio.gather(
                *[
                    self._execute_node(node, plan.objective, outputs, session_id)
                    for node in pending
                ],
                return_exceptions=True,
            )
            for node, result in zip(pending, results, strict=False):
                if isinstance(result, ExecutionSuspended):
                    # Durable plan resume-point so an approved resume continues
                    # the remaining DAG instead of losing it.
                    await self._capture_plan_checkpoint(
                        plan, outputs, node, result, session_id
                    )
                    raise result
                if isinstance(result, BaseException):
                    raise result
                outputs[node.id] = result[1]
        return PlanExecutionResult(plan=plan, node_outputs=outputs)

    async def _capture_plan_checkpoint(
        self,
        plan: ExecutionPlan,
        outputs: dict[str, str],
        node: PlanNode,
        exc: ExecutionSuspended,
        session_id: str,
    ) -> None:
        ws = self.workflow_service
        if ws is None:
            return
        ctx = get_execution_context()
        if ctx.workflow_id is None:
            return
        await ws.save_execution_checkpoint(
            ctx.workflow_id,
            {
                "kind": "plan_execution",
                "plan": plan.model_dump(),
                "completed_outputs": outputs,
                "suspended_node_id": node.id,
                "suspended_agent_id": node.agent_id,
                "suspended_checkpoint_id": exc.info.checkpoint_id,
                "session_id": session_id,
            },
        )

    async def _execute_node(
        self,
        node: PlanNode,
        objective: str,
        outputs: dict[str, str],
        session_id: str,
    ) -> tuple[str, str]:
        if node.node_type == PlanNodeType.HUMAN:
            raise ValueError("Human plan nodes require the durable workflow runtime")
        agent = self.registry.get_agent(node.agent_id or "")
        dependency_context = "\n\n".join(
            f"[{dependency}]\n{outputs[dependency]}"
            for dependency in node.depends_on
        )
        prompt = (
            f"Overall objective: {objective}\n\n"
            f"Your assigned step: {node.description}\n"
        )
        if node.input_data:
            prompt += (
                "\nStructured input:\n"
                + json.dumps(node.input_data, ensure_ascii=False, indent=2)
                + "\n"
            )
        if dependency_context:
            prompt += f"\nResults from prerequisite steps:\n{dependency_context}\n"
        agent_id = node.agent_id or ""
        lock = self._agent_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            if hasattr(agent, "process_structured"):
                outcome = await agent.process_structured(prompt, session_id)
                if getattr(outcome, "status", None) == "suspended":
                    # A remote node returned a durable suspension (its own
                    # service holds the human task). Surface it to the master's
                    # workflow so the plan is checkpointed and later re-driven.
                    raise ExecutionSuspended(_suspension_from_outcome(outcome))
                return node.id, outcome.message
            return node.id, await agent.process(prompt, session_id)


def _suspension_from_outcome(outcome) -> SuspensionInfo:
    meta = getattr(outcome, "metadata", None) or {}
    return SuspensionInfo(
        workflow_id=meta.get("workflow_id") or "",
        step_id=meta.get("step_id") or "",
        checkpoint_id=meta.get("checkpoint_id") or "",
        human_task_id=meta.get("human_task_id") or "",
        policy_decision_id=meta.get("policy_decision_id") or "",
        required_approvals=int(meta.get("required_approvals", 1) or 1),
    )


class WorkflowDriver:
    """Drives DAG execution from durable workflow state (stateless master).

    The master API only *submits* plan-runs (workflow_runs with ``agent_id`` =
    "master" and a ``plan_run`` payload in ``output_data``). This worker picks up
    RUNNING runs and advances ready nodes, persisting progress after each node so
    multiple driver replicas are safe (idempotent per-node).
    """

    def __init__(
        self,
        registry: AgentRegistry,
        workflow_service,
        *,
        agent_id: str = "master",
    ) -> None:
        self.registry = registry
        self.workflow_service = workflow_service
        self.agent_id = agent_id

    async def drain_once(self, limit: int = 20) -> int:
        processed = 0
        for run_id in await self.workflow_service.list_running(self.agent_id, limit):
            try:
                await self._run_workflow(run_id)
                processed += 1
            except Exception as exc:  # pragma: no cover - leave for next pass
                import logging

                logging.getLogger(__name__).exception(
                    "WorkflowDriver run %s failed: %s", run_id, exc
                )
        # Cross-service HITL cascade: poll suspended plan-runs until the remote
        # node's approval resolves (re-drive = idempotent).
        for run_id in await self.workflow_service.list_suspended(self.agent_id, limit):
            try:
                await self._retry_suspended(run_id)
                processed += 1
            except Exception as exc:  # pragma: no cover
                import logging

                logging.getLogger(__name__).exception(
                    "WorkflowDriver retry %s failed: %s", run_id, exc
                )
        return processed

    async def _retry_suspended(self, run_id: str) -> None:
        """Re-drive a suspended plan-run (cross-service HITL cascade).

        The remote node's approval happens in its own service; we poll by
        re-running the durable executor from the completed nodes. Once the
        remote resolves, the node completes and the remaining DAG continues.
        """
        wf = await self.workflow_service.get_internal(run_id)
        if wf is None or wf.get("state") != "suspended":
            return
        state = (wf.get("output_data") or {})
        if state.get("kind") != "plan_run":
            return
        plan = ExecutionPlan.model_validate(state["plan"])
        done = set(state.get("done_nodes") or [])
        completed = {
            k: v for k, v in (state.get("node_outputs") or {}).items() if k in done
        }
        executor = PlanExecutor(self.registry, workflow_service=self.workflow_service)
        token = set_execution_context(
            get_execution_context().child(
                workflow_id=run_id, session_id=state.get("session_id")
            )
        )
        try:
            try:
                plan_result = await executor.execute(
                    plan,
                    session_id=state.get("session_id") or "",
                    resume_outputs=completed,
                )
            except ExecutionSuspended:
                return  # still pending on the remote side; backoff via ordering
            await self.workflow_service.complete(
                run_id, {"message": plan_result.final_output, "status": "completed"}
            )
        finally:
            reset_execution_context(token)

    async def _run_workflow(self, run_id: str) -> None:
        wf = await self.workflow_service.get_internal(run_id)
        if wf is None:
            return
        state = (wf.get("output_data") or {})
        if state.get("kind") != "plan_run":
            return
        plan = ExecutionPlan.model_validate(state["plan"])
        outputs = dict(state.get("node_outputs") or {})
        done = set(state.get("done_nodes") or [])
        session_id = state.get("session_id") or wf.get("session_id") or ""

        advanced = False
        for batch in plan.topological_batches():
            for node in batch:
                if node.id in done or not set(node.depends_on).issubset(done):
                    continue
                token = set_execution_context(
                    get_execution_context().child(workflow_id=run_id, session_id=session_id)
                )
                try:
                    try:
                        outputs[node.id] = await self._execute_node(node, plan.objective, outputs)
                    except ExecutionSuspended as exc:
                        # Remote node suspended for human approval: persist a
                        # durable plan checkpoint + move the run to SUSPENDED.
                        # An approved resume re-drives from the suspended node.
                        await self.workflow_service.save_execution_checkpoint(
                            run_id,
                            {
                                "kind": "plan_execution",
                                "plan": plan.model_dump(),
                                "completed_outputs": outputs,
                                "suspended_node_id": node.id,
                                "suspended_agent_id": node.agent_id,
                                "suspended_workflow_id": exc.info.workflow_id,
                                "session_id": session_id,
                            },
                        )
                        await self.workflow_service.suspend(
                            run_id, "remote node suspended for approval"
                        )
                        return
                    done.add(node.id)
                    advanced = True
                finally:
                    reset_execution_context(token)

        if not advanced:
            return
        await self.workflow_service.update_output_data(
            run_id,
            {
                "kind": "plan_run",
                "plan": plan.model_dump(),
                "node_outputs": outputs,
                "done_nodes": sorted(done),
                "session_id": session_id,
            },
        )
        if done and len(done) == len(plan.nodes):
            result = PlanExecutionResult(plan=plan, node_outputs=outputs)
            await self.workflow_service.complete(
                run_id, {"message": result.final_output, "status": "completed"}
            )

    async def _execute_node(self, node: PlanNode, objective: str, outputs: dict[str, str]) -> str:
        agent = self.registry.get_agent(node.agent_id or "")
        dependency_context = "\n\n".join(
            f"[{dependency}]\n{outputs[dependency]}"
            for dependency in node.depends_on
        )
        prompt = (
            f"Overall objective: {objective}\n\n"
            f"Your assigned step: {node.description}\n"
        )
        if node.input_data:
            prompt += "\nStructured input:\n" + json.dumps(
                node.input_data, ensure_ascii=False, indent=2
            ) + "\n"
        if dependency_context:
            prompt += f"\nResults from prerequisite steps:\n{dependency_context}\n"
        if hasattr(agent, "process_structured"):
            outcome = await agent.process_structured(prompt, "")
            return outcome.message
        return await agent.process(prompt, "")
