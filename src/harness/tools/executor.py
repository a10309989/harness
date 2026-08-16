"""ToolExecutor — sandboxed execution with timeout and error handling."""

import asyncio
import logging

from harness.models.tool import ToolResult
from harness.models.policy import PolicyEffect
from harness.observability.audit import record_audit
from harness.observability.context import child_span
from harness.tools.registry import ToolRegistry
from harness.policy.engine import PolicyEngine
from harness.runtime.services import RuntimeServices
from harness.workflow.context import get_agent_execution_frame

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Executes tools with timeout enforcement and error handling.

    Wraps tool execution in a configurable timeout and converts
    all exceptions into structured ToolResult objects.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        policy_engine: PolicyEngine | None = None,
        runtime_services: RuntimeServices | None = None,
    ) -> None:
        self.registry = registry
        self.runtime_services = runtime_services or RuntimeServices()
        self.policy_engine = policy_engine or self.runtime_services.policy

    async def execute(
        self,
        tool_name: str,
        params: dict,
        *,
        approval_grant_id: str | None = None,
    ) -> ToolResult:
        """Execute a tool with timeout and error handling.

        Args:
            tool_name: Name of the tool to execute.
            params: Parameters to pass to the tool.

        Returns:
            ToolResult with success status, data, and error info.
        """
        try:
            tool = self.registry.get_tool(tool_name)
        except KeyError as e:
            return ToolResult(success=False, error=str(e))

        with child_span():
            policy_engine = self.policy_engine
            if policy_engine is not None:
                policy_decision = await policy_engine.evaluate_tool(
                    tool_name,
                    tool.risk_level,
                    params,
                    risk_tags=tool.risk_tags,
                )
                if not policy_decision.allowed:
                    if (
                        policy_decision.decision
                        in {
                            PolicyEffect.REQUIRE_APPROVAL,
                            PolicyEffect.REQUIRE_TWO_PERSON_APPROVAL,
                        }
                        and approval_grant_id
                    ):
                        human_task_service = self.runtime_services.human_tasks
                        if human_task_service is None:
                            return ToolResult(
                                success=False,
                                error="Approval grant validation is unavailable",
                                policy_decision_id=policy_decision.id,
                                policy_decision=policy_decision.decision,
                            )
                        await human_task_service.validate_and_consume_grant(
                            approval_grant_id,
                            resource_id=tool_name,
                            params=params,
                        )
                    elif (
                        policy_decision.decision
                        in {
                            PolicyEffect.REQUIRE_APPROVAL,
                            PolicyEffect.REQUIRE_TWO_PERSON_APPROVAL,
                        }
                        and self.runtime_services.workflows is not None
                    ):
                        workflow_service = self.runtime_services.workflows
                        from harness.observability.context import get_execution_context

                        if get_execution_context().workflow_id is not None:
                            raise await workflow_service.suspend_for_tool(
                                policy_decision=policy_decision,
                                tool_name=tool_name,
                                params=params,
                                risk_level=str(tool.risk_level),
                                idempotency_mode=str(tool.idempotency_mode),
                                frame=get_agent_execution_frame(),
                            )
                        return await self._blocked_result(
                            tool_name,
                            tool,
                            policy_decision,
                        )
                    else:
                        return await self._blocked_result(
                            tool_name,
                            tool,
                            policy_decision,
                        )
                if not policy_decision.allowed and not approval_grant_id:
                    return await self._blocked_result(
                        tool_name,
                        tool,
                        policy_decision,
                    )
            await record_audit(
                "tool.requested",
                resource_type="tool",
                resource_id=tool_name,
                input_data=params,
                metadata={
                    "timeout_seconds": tool.timeout_seconds,
                    "requires_sandbox": tool.requires_sandbox,
                    "risk_level": str(tool.risk_level),
                },
            )
            try:
                result = await asyncio.wait_for(
                    tool.execute_with_timing(**params),
                    timeout=tool.timeout_seconds,
                )
                await record_audit(
                    "tool.completed" if result.success else "tool.failed",
                    resource_type="tool",
                    resource_id=tool_name,
                    decision="success" if result.success else "failure",
                    reason=result.error,
                    output_data=result.data,
                    metadata={"duration_ms": result.duration_ms},
                )
                if policy_engine is not None:
                    result.policy_decision_id = policy_decision.id
                    result.policy_decision = policy_decision.decision
                return result
            except asyncio.TimeoutError:
                logger.warning(f"Tool '{tool_name}' timed out after {tool.timeout_seconds}s")
                await record_audit(
                    "tool.failed",
                    resource_type="tool",
                    resource_id=tool_name,
                    decision="failure",
                    reason="timeout",
                )
                return ToolResult(
                    success=False,
                    error=f"Tool '{tool_name}' timed out after {tool.timeout_seconds}s",
                )
            except Exception as e:
                logger.error(f"Tool '{tool_name}' execution error: {e}")
                await record_audit(
                    "tool.failed",
                    resource_type="tool",
                    resource_id=tool_name,
                    decision="failure",
                    reason=e.__class__.__name__,
                )
                return ToolResult(success=False, error=str(e))

    @staticmethod
    async def _blocked_result(
        tool_name,
        tool,
        policy_decision,
    ) -> ToolResult:
        await record_audit(
            "tool.blocked",
            resource_type="tool",
            resource_id=tool_name,
            decision=str(policy_decision.decision),
            reason=policy_decision.reason,
            metadata={
                "policy_decision_id": policy_decision.id,
                "risk_level": str(tool.risk_level),
            },
        )
        if policy_decision.decision == PolicyEffect.DENY:
            error = f"Tool '{tool_name}' denied by risk policy"
        else:
            error = (
                f"Tool '{tool_name}' requires approval; "
                f"decision reference: {policy_decision.id}"
            )
        return ToolResult(
            success=False,
            error=error,
            policy_decision_id=policy_decision.id,
            policy_decision=policy_decision.decision,
        )
