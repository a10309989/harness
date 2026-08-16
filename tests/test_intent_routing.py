"""Unit tests for intent classification, routing, and master orchestrator."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from harness.core.events import EventBus
from harness.core.master import (
    IntentClassifier,
    KeywordIntentClassifier,
    MasterAgent,
    MasterOrchestrator,
)
from harness.core.registry import AgentRegistry
from harness.models.intent import IntentCategory, IntentClassification, IntentRoute

# ─── KeywordIntentClassifier ──────────────────────────────────

class TestKeywordIntentClassifier:
    @pytest.fixture
    def classifier(self):
        return KeywordIntentClassifier()

    async def test_classifies_log_analysis_from_chinese(self, classifier):
        result = await classifier.classify("我的测试日志有很多错误，帮我分析一下", {})
        assert result is not None
        assert result.intent == IntentCategory.LOG_ANALYSIS
        assert result.confidence > 0.6

    async def test_classifies_script_generation(self, classifier):
        result = await classifier.classify("帮我生成一个pytest自动化脚本", {})
        assert result is not None
        assert result.intent == IntentCategory.SCRIPT_GENERATION

    async def test_classifies_schedule_execution(self, classifier):
        # Message with multiple execution keywords to meet min_match threshold
        result = await classifier.classify("请执行运行回归测试，立即调度执行测试", {})
        assert result is not None
        assert result.intent == IntentCategory.SCHEDULE_EXECUTION

    async def test_no_match_returns_none(self, classifier):
        result = await classifier.classify("你好", {})
        # "你好" alone may not have enough keyword matches
        assert result is None or result.intent == IntentCategory.GENERAL

    async def test_dynamic_add_keywords(self, classifier):
        classifier.add_keywords(IntentCategory.GENERAL, ["test-new-keyword-xyzzy"])
        result = await classifier.classify("test-new-keyword-xyzzy found here", {})
        # One keyword is not enough to match (need >= 2)
        assert result is None  # single keyword doesn't meet threshold

    async def test_dynamic_set_keywords(self, classifier):
        classifier.set_keywords(IntentCategory.GENERAL, ["custom1", "custom2"])
        result = await classifier.classify("custom1 and custom2 keywords", {})
        assert result is not None
        assert result.intent == IntentCategory.GENERAL

    async def test_confidence_caps_at_max(self, classifier):
        # Create a message with many matching keywords
        classifier.set_keywords(IntentCategory.LOG_ANALYSIS, [
            "日志", "log", "诊断", "failure", "fail", "trace", "error",
            "分析", "analyze", "crash", "timeout", "root cause", "debug",
            "报错", "错误", "崩溃", "超时", "diagnos", "stack"
        ])
        result = await classifier.classify(
            "日志 log 诊断 failure fail trace error 分析 analyze crash timeout root cause debug", {}
        )
        assert result is not None
        assert result.confidence <= 0.95  # max cap

    async def test_chain_of_responsibility_passes_to_next(self):
        """When first classifier returns None, the chain passes to next."""
        class AlwaysNone(IntentClassifier):
            async def _do_classify(self, message, context):
                return None

        class AlwaysGeneral(IntentClassifier):
            async def _do_classify(self, message, context):
                return IntentClassification(intent=IntentCategory.GENERAL, confidence=0.5)

        first = AlwaysNone()
        second = AlwaysGeneral()
        first.set_next(second)

        result = await first.classify("any message", {})
        assert result is not None
        assert result.intent == IntentCategory.GENERAL


# ─── IntentClassification ─────────────────────────────────────

class TestIntentClassification:
    def test_model_dump(self):
        ic = IntentClassification(
            intent=IntentCategory.LOG_ANALYSIS,
            confidence=0.85,
            reasoning="Keyword match: 3 keywords",
        )
        d = ic.model_dump()
        assert d["intent"] == "log_analysis"
        assert d["confidence"] == 0.85

    def test_serialize_deserialize(self):
        ic = IntentClassification(intent=IntentCategory.GENERAL, confidence=0.5)
        json_str = ic.model_dump_json()
        restored = IntentClassification.model_validate_json(json_str)
        assert restored.intent == IntentCategory.GENERAL


# ─── IntentRoute ──────────────────────────────────────────────

class TestIntentRoute:
    def test_defaults(self):
        route = IntentRoute(target_agent="test_agent")
        assert route.target_agent == "test_agent"
        assert route.pre_hooks == []
        assert route.post_hooks == []

    def test_with_hooks(self):
        route = IntentRoute(
            target_agent="log_analyst",
            pre_hooks=["load_patterns"],
            post_hooks=["store_results"],
        )
        assert len(route.pre_hooks) == 1
        assert len(route.post_hooks) == 1


# ─── MasterOrchestrator ───────────────────────────────────────

class TestMasterOrchestrator:
    @pytest.fixture
    def orchestrator(self):
        registry = AgentRegistry()
        event_bus = EventBus()

        async def mock_llm_call(system_prompt, user_message):
            return f"Mock response to: {user_message[:50]}"

        return MasterOrchestrator(
            agent_registry=registry,
            event_bus=event_bus,
            llm_text_call=mock_llm_call,
        )

    async def test_process_general_query(self, orchestrator):
        result = await orchestrator.process("Hello, what can you do?", "session-1")
        assert "Mock response" in result
        assert orchestrator.state == "idle"

    async def test_reset_state(self, orchestrator):
        orchestrator._state = "error"  # simulate error
        orchestrator.reset_state()
        assert orchestrator.state == "idle"

    async def test_dynamic_route_registration(self, orchestrator):
        orchestrator.register_routing_rule(
            IntentCategory.GENERAL,
            target_agent="test_agent",
            pre_hooks=["hook1"],
            post_hooks=["hook2"],
        )
        route = orchestrator.routing_table[IntentCategory.GENERAL]
        assert route.target_agent == "test_agent"
        assert route.pre_hooks == ["hook1"]
        assert route.post_hooks == ["hook2"]

    async def test_routing_table_has_all_intents(self, orchestrator):
        # All standard intents should be in the routing table
        for intent in IntentCategory:
            assert intent in orchestrator.routing_table, f"Missing route for {intent}"


# ─── Capability-driven routing ───────────────────────────────

class _RemoteStyleAgent:
    """A registry agent that exposes capabilities via an A2A-style card."""

    def __init__(self, agent_id: str, capabilities) -> None:
        self.agent_id = agent_id
        self.agent_name = agent_id
        from harness.models.a2a import AgentCard

        self.card = AgentCard(
            agent_id=agent_id,
            name=agent_id,
            capabilities=capabilities,
        )


class TestCapabilityDrivenRouting:
    async def test_routing_built_from_agent_capabilities(self):
        from harness.models.agent import AgentCapability

        registry = AgentRegistry()
        registry.register(
            _RemoteStyleAgent(
                "log_analyst",
                [
                    AgentCapability(
                        name="log_analysis",
                        description="Analyze test logs",
                        keywords=["日志", "log", "error", "诊断", "分析", "错误", "失败"],
                    )
                ],
            )
        )
        registry.register(
            _RemoteStyleAgent(
                "script_generator",
                [
                    AgentCapability(
                        name="script_generation",
                        description="Generate scripts",
                        keywords=["脚本", "pytest", "script"],
                    )
                ],
            )
        )

        async def mock_llm_call(system_prompt, user_message):
            return "{}"

        master = MasterOrchestrator(registry, EventBus(), llm_text_call=mock_llm_call)

        # Routing derived from the cards, not a hardcoded config.
        assert master.routing_table[IntentCategory.LOG_ANALYSIS].target_agent == "log_analyst"
        assert (
            master.routing_table[IntentCategory.SCRIPT_GENERATION].target_agent
            == "script_generator"
        )

        # The classifier uses the capability keywords to route a log request.
        result = await master.classifier_chain.classify("帮我分析日志错误", {})
        assert result is not None
        assert result.intent == IntentCategory.LOG_ANALYSIS

    async def test_empty_registry_falls_back_to_yaml(self):
        registry = AgentRegistry()

        async def mock_llm_call(system_prompt, user_message):
            return "{}"

        master = MasterOrchestrator(registry, EventBus(), llm_text_call=mock_llm_call)
        # No capabilities → YAML config drives the routing.
        assert master.routing_table[IntentCategory.LOG_ANALYSIS].target_agent == "log_analyst"


# ─── MasterAgent backward-compat ──────────────────────────────

class TestMasterAgentBackwardCompat:
    def test_construction(self):
        """MasterAgent should construct without errors."""
        from harness.models.agent import AgentConfig, LLMConfig, VectorConfig
        config = AgentConfig(
            id="master",
            name="Master",
            llm=LLMConfig(provider="test", model="test"),
            vector=VectorConfig(collection_name="test"),
        )
        router = MagicMock()
        router.complete = AsyncMock(return_value=MagicMock(content="test response"))
        registry = AgentRegistry()
        event_bus = EventBus()

        agent = MasterAgent(config, router, registry, event_bus)
        assert agent.agent_id == "master"
        assert agent.agent_name == "Master Orchestrator"
