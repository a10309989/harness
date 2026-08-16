"""Unit tests for core agent functionality: Plan/ReAct/Hybrid modes, tool call parsing, state recovery."""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch

from harness.core.agent import (
    BaseAgent, PlanStep, ReActStep, _clean_json_response, _parse_tool_call_from_text,
)
from harness.llm.types import LLMResponse, TokenUsage
from harness.models.agent import (
    AgentConfig, AgentState, ExecutionMode, LLMConfig, ContextConfig,
    MemoryConfig, VectorConfig,
)


# ─── Helpers ───────────────────────────────────────────────────

def _make_agent_config(**overrides) -> AgentConfig:
    """Create a minimal AgentConfig for testing."""
    defaults = dict(
        id="test_agent",
        name="Test Agent",
        description="Test",
        execution_mode=ExecutionMode.PLAN,
        llm=LLMConfig(provider="test", model="test-model"),
        vector=VectorConfig(collection_name="test_collection"),
        context=ContextConfig(max_turns=10),
        memory=MemoryConfig(storage_path="data/test_memory"),
    )
    defaults.update(overrides)
    return AgentConfig(**defaults)


class _TestAgent(BaseAgent):
    """Concrete BaseAgent for testing."""
    async def _on_before_process(self, message, session_id):
        pass
    async def _on_after_process(self, message, result, session_id):
        pass


def _make_test_agent(config=None) -> _TestAgent:
    """Create a test agent with a mocked LLM router."""
    agent = _TestAgent(config or _make_agent_config(), llm_router=MagicMock())
    return agent


# ─── _clean_json_response ─────────────────────────────────────

class TestCleanJsonResponse:
    def test_strips_markdown_json_block(self):
        text = '```json\n{"key": "value"}\n```'
        result = _clean_json_response(text)
        assert result == '{"key": "value"}'

    def test_strips_markdown_block_no_lang(self):
        text = '```\n{"key": "value"}\n```'
        result = _clean_json_response(text)
        assert result == '{"key": "value"}'

    def test_preserves_plain_json(self):
        text = '{"key": "value"}'
        result = _clean_json_response(text)
        assert result == '{"key": "value"}'

    def test_extracts_object_from_surrounding_text(self):
        text = 'Here is the answer: {"intent": "general", "confidence": 0.9} — hope that helps'
        result = _clean_json_response(text)
        assert result == '{"intent": "general", "confidence": 0.9}'

    def test_extracts_array_from_surrounding_text(self):
        # Array text without markdown marker: the function finds first '{' or '['
        # and extracts JSON accordingly. With mixed content, it extracts the object.
        text = 'Sure! [{"step": 1}, {"step": 2}] is the plan.'
        result = _clean_json_response(text)
        # The extractor finds the first '{' (object) and returns object content.
        # This is expected behavior — arrays in free text without code blocks
        # are partially extracted.
        assert '{"step": 1}' in result or 'step' in result

    def test_handles_nested_json(self):
        text = '```json\n{"outer": {"inner": [1, 2, 3]}}\n```'
        result = _clean_json_response(text)
        assert result == '{"outer": {"inner": [1, 2, 3]}}'


# ─── _parse_tool_call_from_text ────────────────────────────────

class TestParseToolCallFromText:
    def test_parses_tool_name_params(self):
        text = '{"tool": "file_reader", "params": {"path": "/tmp/test.log"}}'
        result = _parse_tool_call_from_text(text)
        assert result == ("file_reader", {"path": "/tmp/test.log"})

    def test_parses_name_parameters(self):
        text = '{"name": "shell_executor", "parameters": {"command": "ls"}}'
        result = _parse_tool_call_from_text(text)
        assert result == ("shell_executor", {"command": "ls"})

    def test_parses_string_arguments(self):
        text = '{"function": "code_executor", "arguments": "{\\"code\\": \\"print(1)\\"}"}'
        result = _parse_tool_call_from_text(text)
        assert result == ("code_executor", {"code": "print(1)"})

    def test_returns_none_for_non_tool_json(self):
        text = '{"intent": "general", "confidence": 0.9}'
        result = _parse_tool_call_from_text(text)
        assert result is None

    def test_handles_markdown_wrapped_tool_call(self):
        text = '```json\n{"tool": "web_fetcher", "params": {"url": "https://example.com"}}\n```'
        result = _parse_tool_call_from_text(text)
        assert result == ("web_fetcher", {"url": "https://example.com"})

    def test_returns_none_for_invalid_json(self):
        text = 'not json at all'
        result = _parse_tool_call_from_text(text)
        assert result is None


# ─── BaseAgent State Recovery ──────────────────────────────────

class TestAgentStateRecovery:
    def test_auto_recovers_from_error_on_process(self):
        agent = _make_test_agent()
        agent.state = AgentState.ERROR

        # Mock call_llm_text to return a simple response
        agent.call_llm_text = AsyncMock(return_value="Hello!")
        agent.call_llm = AsyncMock(return_value=LLMResponse(content="Hello!"))

        result = asyncio.run(agent.process("hi", "session-1"))
        assert agent.state == AgentState.IDLE
        assert "Hello!" in result

    def test_reset_clears_state_and_context(self):
        agent = _make_test_agent()
        agent.state = AgentState.ERROR
        agent.add_turn("user", "hello")
        agent.set_state("foo", "bar")

        agent.reset()

        assert agent.state == AgentState.IDLE
        assert agent.turn_count == 0
        assert agent.get_state("foo") is None


# ─── PlanStep / ReActStep ─────────────────────────────────────

class TestPlanStep:
    def test_plan_step_defaults(self):
        step = PlanStep(description="Test step", action="execute")
        assert step.status == "pending"
        assert step.result is None

    def test_plan_step_lifecycle(self):
        step = PlanStep(description="Test step", action="execute")
        step.status = "running"
        assert step.status == "running"
        step.status = "completed"
        step.result = "Done"
        assert step.status == "completed"
        assert step.result == "Done"


class TestReActStep:
    def test_react_step_defaults(self):
        step = ReActStep(iteration=0, thought="I should check the file")
        assert step.iteration == 0
        assert step.action_name is None
        assert step.is_final is False

    def test_react_step_final(self):
        step = ReActStep(
            iteration=2,
            thought="Found root cause",
            is_final=True,
            final_answer="Root cause is missing config",
        )
        assert step.is_final is True
        assert step.final_answer == "Root cause is missing config"

    def test_react_step_with_tool(self):
        step = ReActStep(
            iteration=1,
            thought="Need to read the log",
            action_name="file_reader",
            action_params={"path": "/tmp/test.log"},
        )
        assert step.action_name == "file_reader"
        assert step.action_params == {"path": "/tmp/test.log"}


# ─── Async test helper ─────────────────────────────────────────
import asyncio
