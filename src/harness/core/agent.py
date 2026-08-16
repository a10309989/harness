"""BaseAgent — composes all 6 mixins with Plan/ReAct/Hybrid execution.

This is the architectural centerpiece of the Harness framework.
Every agent (Master, sub-agents) inherits from this class.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from harness.core.mixins.context import ContextMixin
from harness.core.mixins.knowledge import KnowledgeMixin
from harness.core.mixins.memory import MemoryMixin
from harness.core.mixins.prompt import PromptMixin
from harness.core.mixins.tool import ToolMixin
from harness.core.mixins.vector import VectorMixin
from harness.llm.base import AbstractLLMProvider
from harness.llm.router import ModelRouter
from harness.llm.types import LLMRequest, LLMResponse
from harness.models.agent import AgentConfig, AgentState, ExecutionMode, StopCondition
from harness.models.artifact import AgentOutcome, ArtifactDraft, ArtifactVersionRef
from harness.models.tool import ToolResult
from harness.observability.audit import record_audit
from harness.runtime.services import RuntimeServices
from harness.utils.id_gen import generate_id

logger = logging.getLogger(__name__)


@dataclass
class PlanStep:
    """A single step in a Plan-mode execution."""

    step_id: str = field(default_factory=generate_id)
    description: str = ""
    action: str = ""
    status: str = "pending"  # pending, running, completed, failed
    result: str | None = None


@dataclass
class ReActStep:
    """A single ReAct cycle: Thought → Action → Observation."""

    iteration: int
    thought: str = ""
    action_name: str | None = None
    action_params: dict | None = None
    observation: str | None = None
    is_final: bool = False
    final_answer: str | None = None


class BaseAgent(
    PromptMixin,
    ContextMixin,
    MemoryMixin,
    VectorMixin,
    KnowledgeMixin,
    ToolMixin,
    ABC,
):
    """Base agent composing all six pluggable capabilities.

    Supports three execution modes:
    - PLAN: Generate a plan, execute each step, aggregate results.
    - REACT: Thought → Action → Observation loop until final answer.
    - HYBRID: Generate a plan, then ReAct within each step.
    """

    agent_id: str
    agent_name: str
    config: AgentConfig
    llm_router: ModelRouter
    state: AgentState

    def __init__(
        self,
        config: AgentConfig,
        llm_router: ModelRouter,
        runtime_services: RuntimeServices | None = None,
    ) -> None:
        """Initialize the agent from configuration.

        Args:
            config: Full agent configuration.
            llm_router: ModelRouter with primary and fallback LLM providers.
        """
        self.agent_id = config.id
        self.agent_name = config.name
        self.config = config
        self.llm_router = llm_router
        self.runtime_services = runtime_services or RuntimeServices()
        self.state = AgentState.IDLE

        # Initialize each mixin from config
        self._init_prompt(config)
        self._init_context(max_turns=config.context.max_turns)
        self._init_memory(
            storage_path=config.memory.storage_path,
            ttl_seconds=config.memory.short_term_ttl_seconds,
            long_term_enabled=config.memory.long_term_enabled,
        )
        self._init_vector(
            collection_name=config.vector.collection_name,
            embedding_model=config.vector.embedding_model,
            embedding_provider=config.vector.embedding_provider,
            persist_directory=config.vector.persist_directory,
        )
        self._init_knowledge(config.knowledge_bases)
        self._init_tools(config.tools, self.runtime_services)

    # ─── Public API ─────────────────────────────────────────────

    async def process(self, message: str, session_id: str) -> str:
        """Compatibility API returning only the human-readable message."""
        from harness.workflow.context import agent_execution_scope

        with agent_execution_scope(self.agent_id, message, session_id):
            outcome = await self.process_structured(message, session_id)
        return outcome.message

    async def process_structured(
        self,
        message: str,
        session_id: str,
    ) -> AgentOutcome:
        """Process an incoming message using the configured execution mode.

        Args:
            message: The user's input message.
            session_id: Current session identifier.

        Returns:
            A structured outcome containing the response and immutable artifacts.
        """
        # Auto-recover from ERROR state on next message
        if self.state == AgentState.ERROR:
            logger.warning(f"Agent {self.agent_name} was in ERROR state — auto-recovering")
            self.state = AgentState.IDLE

        self.state = AgentState.PROCESSING
        self.add_turn("user", message)
        await self._on_before_process(message, session_id)
        await record_audit(
            "agent.started",
            resource_type="agent",
            resource_id=self.agent_id,
            input_data={"message": message},
            metadata={"execution_mode": self.config.execution_mode},
        )

        try:
            mode = self.config.execution_mode
            if mode == ExecutionMode.PLAN:
                result = await self._run_plan_mode(message, session_id)
            elif mode == ExecutionMode.REACT:
                result = await self._run_react_loop(message, session_id)
            else:  # HYBRID
                result = await self._run_hybrid_mode(message, session_id)

            self.add_turn("assistant", result)
            await self._on_after_process(message, result, session_id)
            artifacts = await self._persist_artifact_drafts(
                self.build_artifact_drafts(message, result, session_id)
            )
            self.state = AgentState.IDLE
            await record_audit(
                "agent.completed",
                resource_type="agent",
                resource_id=self.agent_id,
                output_data={"result": result},
                metadata={"execution_mode": self.config.execution_mode},
            )
            return AgentOutcome(
                message=result,
                artifacts=artifacts,
                metadata={
                    "agent_id": self.agent_id,
                    "session_id": session_id,
                    "execution_mode": str(self.config.execution_mode),
                },
            )

        except Exception as e:
            from harness.models.workflow import ExecutionSuspended

            if isinstance(e, ExecutionSuspended):
                self.state = AgentState.WAITING
                await record_audit(
                    "agent.suspended",
                    resource_type="agent",
                    resource_id=self.agent_id,
                    decision="waiting",
                    metadata=e.info.model_dump(),
                )
                raise
            logger.error(f"Agent {self.agent_name} error: {e}")
            self.state = AgentState.ERROR
            error_msg = f"Processing error: {e}"
            self.add_turn("system", error_msg)
            await record_audit(
                "agent.failed",
                resource_type="agent",
                resource_id=self.agent_id,
                decision="failure",
                reason=e.__class__.__name__,
            )
            return AgentOutcome(
                message=error_msg,
                status="failed",
                metadata={"agent_id": self.agent_id, "session_id": session_id},
            )

    async def resume_after_tool(
        self,
        *,
        original_message: str,
        tool_name: str,
        tool_result: ToolResult,
        session_id: str,
    ) -> str:
        """Reconstruct an Agent continuation from a durable tool checkpoint."""
        observation = (
            json.dumps(tool_result.data, ensure_ascii=False, indent=2)
            if tool_result.data is not None
            else tool_result.error or "Tool returned no output"
        )
        self.add_turn(
            "observation",
            observation,
            {
                "resumed": True,
                "tool_name": tool_name,
                "policy_decision_id": tool_result.policy_decision_id,
            },
        )
        resume_message = (
            f"Resume the original task after the approved tool call.\n\n"
            f"Original task: {original_message}\n\n"
            f"Tool already executed: {tool_name}\n"
            f"Observation:\n{observation}\n\n"
            "Continue from this observation. Do not repeat the same tool call "
            "unless new input makes it necessary."
        )
        return await self.process(resume_message, session_id)

    def build_artifact_drafts(
        self,
        message: str,
        result: str,
        session_id: str,
    ) -> list[ArtifactDraft]:
        """Return immutable output drafts for domain agents that produce artifacts."""
        return []

    async def _persist_artifact_drafts(
        self,
        drafts: list[ArtifactDraft],
    ) -> list[ArtifactVersionRef]:
        if not drafts:
            return []
        service = self.runtime_services.artifacts
        if service is None:
            return []
        return [await service.create(draft) for draft in drafts]

    def reset(self) -> None:
        """Reset the agent state to IDLE. Allows recovery from ERROR state.

        Clears the agent state and clears the conversation context, making
        the agent ready for a fresh interaction.
        """
        self.state = AgentState.IDLE
        self.clear_context()
        logger.info(f"Agent {self.agent_name} reset to IDLE state")

    async def call_llm(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """Unified LLM call with context injection.

        Args:
            system_prompt: System prompt for this call.
            user_message: The user message.
            tools: Optional tool definitions for function calling.

        Returns:
            The full LLMResponse with content, tool_calls, usage, etc.
        """
        request = LLMRequest(
            system_prompt=system_prompt,
            user_message=user_message,
            messages=self.get_context_for_llm(),
            tools=tools or self.get_tool_definitions(),
            temperature=self.config.llm.temperature,
            max_tokens=self.config.llm.max_tokens,
        )
        return await self.llm_router.complete(request)

    async def call_llm_text(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[dict] | None = None,
    ) -> str:
        """Convenience: call LLM and return content text only.

        Args:
            system_prompt: System prompt for this call.
            user_message: The user message.
            tools: Optional tool definitions.

        Returns:
            The LLM's response content as a string.
        """
        response = await self.call_llm(system_prompt, user_message, tools)
        return response.content

    # ─── Plan Mode ──────────────────────────────────────────────

    async def _run_plan_mode(self, message: str, session_id: str) -> str:
        """Generate a plan, execute each step sequentially, aggregate results."""
        plan = await self._generate_plan(message)
        self.add_turn("system", f"Generated plan with {len(plan)} steps", {"plan": str(plan)})

        results: list[str] = []
        for i, step in enumerate(plan):
            step.status = "running"
            self.add_turn("system", f"Executing step {i + 1}/{len(plan)}: {step.description}")
            try:
                step.result = await self._execute_plan_step(step)
                step.status = "completed"
                results.append(step.result)
            except Exception as e:
                step.status = "failed"
                step.result = str(e)
                results.append(f"[FAILED] {step.description}: {e}")

        return self._aggregate_plan_results(plan, results)

    async def _generate_plan(self, message: str) -> list[PlanStep]:
        """Use LLM to break down the task into a step-by-step plan."""
        system_prompt = self.build_system_prompt({"task": message})
        prompt = (
            f"Task: {message}\n\n"
            "Break this task into a numbered list of concrete, executable steps. "
            "Each step should be a single action. Output as JSON list with 'description' and 'action' fields."
        )
        response_text = await self.call_llm_text(system_prompt, prompt)

        # Parse LLM response into PlanSteps
        try:
            steps_data = json.loads(_clean_json_response(response_text))
            if isinstance(steps_data, list):
                return [
                    PlanStep(description=s.get("description", ""), action=s.get("action", ""))
                    for s in steps_data
                ]
        except json.JSONDecodeError:
            pass

        # Fallback: create a single step
        return [PlanStep(description=message, action="process")]

    async def _execute_plan_step(self, step: PlanStep) -> str:
        """Execute a single plan step via LLM."""
        system_prompt = self.build_system_prompt({})
        return await self.call_llm_text(system_prompt, f"Execute: {step.description}\nAction: {step.action}")

    def _aggregate_plan_results(self, plan: list[PlanStep], results: list[str]) -> str:
        """Combine step results into a final response."""
        parts = []
        for step, result in zip(plan, results):
            status_icon = "✅" if step.status == "completed" else "❌"
            parts.append(f"{status_icon} **{step.description}**\n{result}")
        return "\n\n".join(parts)

    # ─── ReAct Mode ─────────────────────────────────────────────

    async def _run_react_loop(self, message: str, session_id: str) -> str:
        """Thought → Action → Observation loop until final answer."""
        max_iterations = self.config.react_max_iterations
        no_progress_limit = self._get_stop_condition("no_progress_rounds", 3)
        no_progress_count = 0
        last_content = ""

        for i in range(max_iterations):
            step = await self._think(message, i)

            # The LLM gave a final answer either via the FINAL_ANSWER marker or
            # by responding with plain text and no tool call. A ReAct turn with
            # no requested action *is* the answer — return it instead of
            # discarding the thought and looping through the remaining budget.
            if step.is_final or step.final_answer or not step.action_name:
                self.add_turn("system", f"Final answer after {i + 1} iterations")
                return step.final_answer or step.thought

            observation = await self._act(step)
            step.observation = observation
            self.add_turn("observation", observation)

            # Check for progress
            if observation.strip() == last_content.strip():
                no_progress_count += 1
                if no_progress_count >= no_progress_limit:
                    return await self._force_final_answer(message, i + 1)
            else:
                no_progress_count = 0
                last_content = observation

        # Max iterations reached
        return await self._force_final_answer(message, max_iterations)

    async def _think(self, original_message: str, iteration: int) -> ReActStep:
        """Produce a Thought → Action pair via LLM.

        Now properly handles both text-based FINAL_ANSWER patterns and
        native function-calling tool_use responses.
        """
        tools = self.get_tool_definitions()
        tool_names = [t.get("name", "") for t in tools] if tools else []

        prompt = (
            f"Original task: {original_message}\n\n"
            f"Iteration: {iteration + 1}\n\n"
            "You are in a ReAct (Reasoning + Acting) loop. "
            "Think about what to do next, then either:\n"
            "1. Call a tool using one of the available functions.\n"
            "2. If you have the final answer, respond with: FINAL_ANSWER: <your answer>\n\n"
            f"Available tools: {', '.join(tool_names) if tool_names else 'None'}"
        )

        system_prompt = self.build_system_prompt({"task": original_message})
        response = await self.call_llm(system_prompt, prompt, tools=tools if tools else None)
        content = response.content

        # Priority 1: Native function-calling tool_use (Anthropic/OpenAI)
        if response.tool_calls:
            # Take the first tool call
            tc = response.tool_calls[0]
            return ReActStep(
                iteration=iteration,
                thought=content,
                action_name=tc.get("name", ""),
                action_params=tc.get("input", {}),
            )

        # Priority 2: Text-based FINAL_ANSWER marker
        if "FINAL_ANSWER:" in content:
            final = content.split("FINAL_ANSWER:", 1)[1].strip()
            return ReActStep(
                iteration=iteration,
                thought=content,
                is_final=True,
                final_answer=final,
            )

        # Priority 3: Try to parse tool call from JSON in content
        parsed = _parse_tool_call_from_text(content)
        if parsed:
            return ReActStep(
                iteration=iteration,
                thought=content,
                action_name=parsed[0],
                action_params=parsed[1],
            )

        # No tool call, no final answer — return thought as-is
        return ReActStep(iteration=iteration, thought=content)

    async def _act(self, step: ReActStep) -> str:
        """Execute the tool call and return the observation."""
        if not step.action_name:
            return step.thought  # No tool to call, return thought as observation

        result: ToolResult = await self.execute_tool(step.action_name, step.action_params or {})
        if result.success and result.data:
            return json.dumps(result.data, ensure_ascii=False, indent=2)
        return result.error or "Tool returned no output"

    async def _force_final_answer(self, message: str, iterations: int) -> str:
        """Force the agent to produce a final answer after max iterations."""
        prompt = (
            f"Task: {message}\n\n"
            f"You have completed {iterations} iterations. "
            "Based on all observations so far, provide your best final answer."
        )
        system_prompt = self.build_system_prompt({})
        return await self.call_llm_text(system_prompt, prompt)

    # ─── Hybrid Mode ────────────────────────────────────────────

    async def _run_hybrid_mode(self, message: str, session_id: str) -> str:
        """Generate a plan, then run ReAct within each plan step."""
        plan = await self._generate_plan(message)

        all_results: list[str] = []
        for i, step in enumerate(plan):
            self.add_turn("system", f"Step {i + 1}/{len(plan)}: {step.description}")
            try:
                step_result = await self._run_react_loop(
                    f"{message}\n\nFocus on this sub-task: {step.description}", session_id
                )
                all_results.append(f"✅ {step.description}\n{step_result}")
            except Exception as e:
                all_results.append(f"❌ {step.description}: {e}")

        return "\n\n---\n\n".join(all_results)

    # ─── Helpers ─────────────────────────────────────────────────

    def _get_stop_condition(self, cond_type: str, default: int) -> int:
        """Extract a stop condition value from config."""
        for cond in self.config.react_stop_conditions:
            if cond.type == cond_type:
                return cond.value
        return default

    @abstractmethod
    async def _on_before_process(self, message: str, session_id: str) -> None:
        """Hook: called before processing begins. Override in subclasses."""
        ...

    @abstractmethod
    async def _on_after_process(self, message: str, result: str, session_id: str) -> None:
        """Hook: called after processing completes. Override in subclasses."""
        ...


# ─── Module-level helpers ─────────────────────────────────────

_MARKDOWN_JSON_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def _clean_json_response(text: str) -> str:
    """Strip markdown code blocks from LLM JSON responses.

    Handles:
    - ```json\\n{...}\\n```
    - ```\\n{...}\\n```
    - Plain JSON text
    """
    text = text.strip()
    # Try to extract from markdown code block
    match = _MARKDOWN_JSON_RE.search(text)
    if match:
        return match.group(1).strip()
    # Find the first '{' or '[' — strip any surrounding text
    for start_char in ("{", "["):
        idx = text.find(start_char)
        if idx >= 0:
            end_char = "}" if start_char == "{" else "]"
            end_idx = text.rfind(end_char)
            if end_idx > idx:
                return text[idx:end_idx + 1]
    return text


def _parse_tool_call_from_text(content: str) -> tuple[str, dict] | None:
    """Try to parse a tool call from LLM text content.

    Supports patterns like:
    - {"tool": "file_reader", "params": {"path": "..."}}
    - {"name": "file_reader", "parameters": {"path": "..."}}
    """
    try:
        cleaned = _clean_json_response(content)
        data = json.loads(cleaned)
        if isinstance(data, dict):
            tool_name = data.get("tool") or data.get("name") or data.get("function")
            params = data.get("params") or data.get("parameters") or data.get("arguments") or data.get("input", {})
            if isinstance(params, str):
                params = json.loads(params)
            if tool_name and isinstance(params, dict):
                return (tool_name, params)
    except (json.JSONDecodeError, TypeError):
        pass
    return None
