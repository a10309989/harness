"""Shared approved-tool recovery logic for legacy and Temporal workers."""

from __future__ import annotations

from dataclasses import replace

from harness.models.workflow import ExecutionSuspended, IdempotencyMode
from harness.observability.context import (
    SYSTEM_ACTOR,
    ExecutionContext,
    new_id,
    reset_execution_context,
    set_execution_context,
)


class ApprovedToolResumer:
    """Resume a checkpointed tool call after its approval grant is active."""

    def __init__(
        self,
        workflow_service,
        human_task_service,
        agent_registry,
        session_manager,
    ) -> None:
        self.workflow_service = workflow_service
        self.human_task_service = human_task_service
        self.agent_registry = agent_registry
        self.session_manager = session_manager

    async def resume(self, workflow_id: str) -> None:
        workflow = await self.workflow_service.get_internal(workflow_id)
        if workflow is None:
            return
        token = set_execution_context(
            ExecutionContext(
                trace_id=workflow["trace_id"],
                span_id=new_id(),
                session_id=workflow["session_id"],
                workflow_id=workflow_id,
                actor=replace(SYSTEM_ACTOR, tenant_id=workflow["tenant_id"]),
            )
        )
        try:
            checkpoint_row, checkpoint = await self.workflow_service.load_checkpoint(
                workflow_id
            )
            if checkpoint.get("kind") == "plan_execution":
                await self._resume_plan(workflow, checkpoint, workflow_id)
                return
            grant = await self.human_task_service.get_active_grant(workflow_id)
            if grant is None:
                await self.workflow_service.recovery_required(
                    workflow_id,
                    "No active approval grant is available for resumption",
                )
                return
            agent_id = checkpoint.get("agent_id")
            if not agent_id:
                await self.workflow_service.recovery_required(
                    workflow_id,
                    "Checkpoint does not identify the originating agent",
                )
                return
            try:
                agent = self.agent_registry.get_agent(agent_id)
            except KeyError:
                await self.workflow_service.recovery_required(
                    workflow_id,
                    f"Agent '{agent_id}' is unavailable",
                )
                return
            await self.workflow_service.mark_resuming(workflow_id)
            await self.workflow_service.mark_step_running(checkpoint_row["step_id"])
            try:
                result = await agent.tool_executor.execute(
                    checkpoint["tool_name"], checkpoint["params"], approval_grant_id=grant["id"]
                )
            except Exception as exc:
                if checkpoint.get("idempotency_mode") == str(IdempotencyMode.NON_IDEMPOTENT):
                    await self.workflow_service.recovery_required(
                        workflow_id,
                        f"Non-idempotent tool recovery requires review: {exc}",
                    )
                else:
                    await self.workflow_service.fail(workflow_id, str(exc))
                return
            if not result.success:
                await self.workflow_service.fail(workflow_id, result.error or "Approved tool execution failed")
                return
            await self.workflow_service.mark_step_completed(
                checkpoint_row["step_id"],
                result.model_dump(),
            )
            try:
                response = await agent.resume_after_tool(
                    original_message=checkpoint.get("original_message") or "",
                    tool_name=checkpoint["tool_name"],
                    tool_result=result,
                    session_id=checkpoint.get("session_id") or workflow["session_id"] or "",
                )
            except ExecutionSuspended:
                return
            await self.workflow_service.complete(
                workflow_id,
                {"message": response, "tool_result": result.model_dump()},
            )
            if workflow["session_id"]:
                self.session_manager.add_turn(workflow["session_id"], "assistant", response)
        except Exception as exc:
            current = await self.workflow_service.get_internal(workflow_id)
            if current and current["state"] not in {
                "completed",
                "failed",
                "cancelled",
                "recovery_required",
            }:
                await self.workflow_service.recovery_required(workflow_id, str(exc))
            raise
        finally:
            reset_execution_context(token)

    async def _resume_plan(
        self,
        workflow: dict,
        plan_ckpt: dict,
        workflow_id: str,
    ) -> None:
        """Resume a master-driven DAG that suspended mid-execution.

        Local path: the suspended node's tool was checkpointed locally — replay
        it with its approval grant and feed the output back as the node's result.
        Remote path: the node's HITL lives in the remote service — re-drive from
        the suspended node; if it re-suspends (still pending), leave the workflow
        suspended for another approval. Either way the remaining DAG continues.
        """
        from harness.core.execution import PlanExecutor
        from harness.models.plan import ExecutionPlan

        plan = ExecutionPlan.model_validate(plan_ckpt["plan"])
        completed = dict(plan_ckpt.get("completed_outputs") or {})
        suspended_node = plan_ckpt.get("suspended_node_id")
        session_id = plan_ckpt.get("session_id") or workflow.get("session_id") or ""

        await self.workflow_service.mark_resuming(workflow_id)

        # Local tool replay (only when the suspended agent is a local agent).
        tool_checkpoint_id = plan_ckpt.get("suspended_checkpoint_id")
        agent_id = plan_ckpt.get("suspended_agent_id")
        if tool_checkpoint_id and agent_id:
            try:
                agent = self.agent_registry.get_agent(agent_id)
            except KeyError:
                agent = None
            if agent is not None and hasattr(agent, "tool_executor"):
                tool_row, tool_ckpt = await self.workflow_service.load_checkpoint_by_id(
                    tool_checkpoint_id
                )
                grant = await self.human_task_service.get_active_grant(workflow_id)
                if grant is None:
                    await self.workflow_service.recovery_required(
                        workflow_id, "No active approval grant is available for resumption"
                    )
                    return
                await self.workflow_service.mark_step_running(tool_row["step_id"])
                try:
                    result = await agent.tool_executor.execute(
                        tool_ckpt["tool_name"],
                        tool_ckpt["params"],
                        approval_grant_id=grant["id"],
                    )
                except Exception as exc:
                    if tool_ckpt.get("idempotency_mode") == str(IdempotencyMode.NON_IDEMPOTENT):
                        await self.workflow_service.recovery_required(
                            workflow_id,
                            f"Non-idempotent tool recovery requires review: {exc}",
                        )
                    else:
                        await self.workflow_service.fail(workflow_id, str(exc))
                    return
                if not result.success:
                    await self.workflow_service.fail(
                        workflow_id, result.error or "Approved tool execution failed"
                    )
                    return
                await self.workflow_service.mark_step_completed(
                    tool_row["step_id"], result.model_dump()
                )
                try:
                    response = await agent.resume_after_tool(
                        original_message=tool_ckpt.get("original_message") or "",
                        tool_name=tool_ckpt["tool_name"],
                        tool_result=result,
                        session_id=session_id,
                    )
                except ExecutionSuspended:
                    return
                if suspended_node:
                    completed[suspended_node] = response

        executor = PlanExecutor(
            self.agent_registry, workflow_service=self.workflow_service
        )
        try:
            plan_result = await executor.execute(
                plan,
                session_id=session_id,
                resume_outputs=completed,
            )
        except ExecutionSuspended:
            # A node re-suspended (e.g. remote HITL still pending). The executor
            # already re-checkpointed the plan; wait for another approval.
            await self.workflow_service.suspend(workflow_id, "plan node re-suspended")
            return
        await self.workflow_service.complete(
            workflow_id,
            {"message": plan_result.final_output, "status": "completed"},
        )
        if workflow["session_id"]:
            self.session_manager.add_turn(
                workflow["session_id"], "assistant", plan_result.final_output
            )
