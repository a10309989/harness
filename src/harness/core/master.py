"""MasterAgent — intent classification, routing, and sub-agent orchestration.

Architecture: The MasterAgent uses COMPOSITION rather than inheritance.
It contains a BaseAgent instance for LLM-based query handling and delegates
intent classification and routing to pluggable classifier chains.
The routing table is loaded from external YAML configuration.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from harness.core.events import Event, EventBus
from harness.core.planner import should_plan
from harness.core.registry import AgentRegistry
from harness.models.agent import AgentConfig, AgentState
from harness.models.artifact import AgentOutcome
from harness.models.intent import IntentCategory, IntentClassification, IntentRoute
from harness.observability.audit import record_audit
from harness.observability.context import (
    get_execution_context,
    reset_execution_context,
    set_execution_context,
)
from harness.utils.config import load_intent_routing_config

logger = logging.getLogger(__name__)


def _agent_capabilities(agent) -> list:
    """Return an agent's declared capabilities (remote card or local config)."""
    card = getattr(agent, "card", None)
    if card is not None and getattr(card, "capabilities", None):
        return card.capabilities
    config = getattr(agent, "config", None)
    if config is not None and getattr(config, "capabilities", None):
        return config.capabilities
    return []


def _is_ascii(text: str) -> bool:
    return all(ord(ch) < 128 for ch in text)


class IntentClassifier(ABC):
    """Chain of Responsibility link for intent classification."""

    next_classifier: IntentClassifier | None = None

    def set_next(self, classifier: IntentClassifier) -> IntentClassifier:
        self.next_classifier = classifier
        return classifier

    async def classify(self, message: str, context: dict) -> IntentClassification | None:
        result = await self._do_classify(message, context)
        if result is not None:
            return result
        if self.next_classifier is not None:
            return await self.next_classifier.classify(message, context)
        return None

    @abstractmethod
    async def _do_classify(self, message: str, context: dict) -> IntentClassification | None:
        ...


class KeywordIntentClassifier(IntentClassifier):
    """Fast keyword-based classifier as pre-filter before LLM.

    Keywords are loaded from external YAML configuration (config/intent_routing.yaml).
    """

    def __init__(self, routing_config: dict | None = None) -> None:
        self._keyword_map: dict[IntentCategory, list[str]] = {}
        self._min_matches: int = 2
        self._base_confidence: float = 0.7
        self._per_match_confidence: float = 0.05
        self._max_confidence: float = 0.95
        self._load_from_config(routing_config)

    def _load_from_config(self, routing_config: dict | None) -> None:
        """Build keyword map from external routing configuration."""
        if routing_config is None:
            routing_config = load_intent_routing_config()

        self._min_matches = routing_config.get("keyword_min_matches", 2)
        kconf = routing_config.get("keyword_confidence") or {}
        self._base_confidence = kconf.get("base", 0.7)
        self._per_match_confidence = kconf.get("per_match", 0.05)
        self._max_confidence = kconf.get("max", 0.95)

        intents = routing_config.get("intents") or {}
        for intent_name, intent_cfg in intents.items():
            intent_cfg = intent_cfg or {}
            try:
                category = IntentCategory(intent_name)
            except ValueError:
                logger.warning(f"Unknown intent category in routing config: {intent_name}")
                continue

            keywords = intent_cfg.get("keywords") or {}
            all_keywords: list[str] = []
            if isinstance(keywords, dict):
                for kw_list in keywords.values():
                    if isinstance(kw_list, list):
                        all_keywords.extend(kw_list)
            elif isinstance(keywords, list):
                all_keywords = keywords
            self._keyword_map[category] = all_keywords

    async def _do_classify(self, message: str, context: dict) -> IntentClassification | None:
        msg_lower = message.lower()
        if self._is_generate_scripts_from_artifact_request(message, msg_lower):
            return IntentClassification(
                intent=IntentCategory.SCRIPT_GENERATION,
                confidence=0.97,
                reasoning="Matched artifact-based script generation request",
            )
        if self._is_generate_test_cases_from_artifact_request(message, msg_lower):
            return IntentClassification(
                intent=IntentCategory.TEST_CASE_GENERATION,
                confidence=0.97,
                reasoning="Matched artifact-based test-case generation request",
            )
        if self._is_requirement_strategy_request(message, msg_lower):
            return IntentClassification(
                intent=IntentCategory.REQUIREMENTS_ANALYSIS,
                confidence=0.96,
                reasoning="Matched requirement document test-strategy request",
            )
        max_score = 0
        best_intent: IntentCategory | None = None

        for intent, keywords in self._keyword_map.items():
            score = sum(1 for kw in keywords if kw.lower() in msg_lower)
            if score > max_score:
                max_score = score
                best_intent = intent

        if max_score >= self._min_matches and best_intent is not None:
            confidence = min(self._base_confidence + max_score * self._per_match_confidence, self._max_confidence)
            return IntentClassification(
                intent=best_intent,
                confidence=confidence,
                reasoning=f"Keyword match: {max_score} keywords",
            )
        return None

    def _is_requirement_strategy_request(self, message: str, msg_lower: str) -> bool:
        has_document = "知识库" in message or "文档" in message or ".md" in msg_lower or "document" in msg_lower
        has_strategy = "测试策略" in message or "test strategy" in msg_lower
        has_requirement = "需求" in message or "requirement" in msg_lower
        return has_document and has_strategy and has_requirement

    def _is_generate_test_cases_from_artifact_request(self, message: str, msg_lower: str) -> bool:
        has_case = (
            "生成用例" in message
            or "测试用例" in message
            or "用例" in message
            or "generate test cases" in msg_lower
            or "test case" in msg_lower
        )
        has_artifact = (
            "artifact" in msg_lower
            or "version_id" in msg_lower
            or "artifact_version_id" in msg_lower
        )
        return has_case and has_artifact

    def _is_generate_scripts_from_artifact_request(self, message: str, msg_lower: str) -> bool:
        has_script = (
            "生成脚本" in message
            or "自动化脚本" in message
            or "脚本" in message
            or "generate scripts" in msg_lower
            or "generate script" in msg_lower
            or "pytest" in msg_lower
            or "playwright" in msg_lower
        )
        has_artifact = (
            "artifact" in msg_lower
            or "version_id" in msg_lower
            or "artifact_version_id" in msg_lower
        )
        return has_script and has_artifact

    def add_keywords(self, intent: IntentCategory, keywords: list[str]) -> None:
        """Dynamically add keywords for an intent category."""
        if intent not in self._keyword_map:
            self._keyword_map[intent] = []
        self._keyword_map[intent].extend(keywords)

    def set_keywords(self, intent: IntentCategory, keywords: list[str]) -> None:
        """Replace keywords for an intent category."""
        self._keyword_map[intent] = list(keywords)


class LLMIntentClassifier(IntentClassifier):
    """Uses LLM with few-shot prompt to classify user intent."""

    def __init__(self, llm_call, intent_labels: list[str] | None = None) -> None:
        self._llm_call = llm_call
        from harness.core.agent import _clean_json_response
        self._clean_json = _clean_json_response
        self._intent_labels = intent_labels or [
            "requirements_analysis", "test_case_generation", "script_generation",
            "schedule_execution", "log_analysis", "general",
        ]

    async def _do_classify(self, message: str, context: dict) -> IntentClassification | None:
        label_descriptions = "\n".join(f"- {l}" for l in self._intent_labels)
        prompt = (
            "Classify the following user message into one of these intent categories:\n\n"
            f"{label_descriptions}\n\n"
            f"User message: {message}\n\n"
            "Respond with a JSON object: {\"intent\": \"<category>\", \"confidence\": <0-1>, \"reasoning\": \"...\"}"
        )
        try:
            response = await self._llm_call(
                "You are an intent classifier for a test automation system.",
                prompt,
            )
            # Response may be LLMResponse or string
            if hasattr(response, 'content'):
                content = response.content
            else:
                content = str(response)
            cleaned = self._clean_json(content)
            data = json.loads(cleaned)
            return IntentClassification(
                intent=IntentCategory(data.get("intent", "general")),
                confidence=float(data.get("confidence", 0.5)),
                reasoning=data.get("reasoning", ""),
            )
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Intent classification failed: {e}")
            return None


class MasterOrchestrator:
    """Orchestrates intent classification, routing, and sub-agent delegation.

    Uses COMPOSITION: contains a BaseAgent for LLM handling and delegates
    to the AgentRegistry for routing. This is NOT a BaseAgent subclass —
    it's an orchestrator that coordinates agents.

    Pipeline:
    1. KeywordIntentClassifier (fast pre-filter, from YAML config)
    2. LLMIntentClassifier (precise LLM classification)
    3. Fallback → GENERAL intent
    4. Route to target sub-agent via AgentRegistry
    5. Aggregate and return response
    """

    orchestrator_id: str = "master_orchestrator"
    orchestrator_name: str = "Master Orchestrator"

    agent_registry: AgentRegistry
    event_bus: EventBus
    classifier_chain: IntentClassifier
    routing_table: dict[IntentCategory, IntentRoute]
    _llm_text_call: Any  # callable for text-only LLM calls
    _state: AgentState
    _min_confidence: float

    def __init__(
        self,
        agent_registry: AgentRegistry,
        event_bus: EventBus,
        llm_text_call=None,
        routing_config: dict | None = None,
    ) -> None:
        self.agent_registry = agent_registry
        self.event_bus = event_bus
        self._llm_text_call = llm_text_call
        self._runtime_state: dict[str, Any] = {}
        self._state = AgentState.IDLE

        # Routing is capability-driven: build the intent → agent map + keyword
        # classifier from each agent's declared capabilities (AgentCard), so the
        # routing follows agent self-description instead of a hardcoded config.
        # Falls back to config/intent_routing.yaml when no capabilities exist.
        if routing_config is None:
            routing_config = self._derive_routing_config_from_registry()
            if routing_config is None:
                try:
                    routing_config = load_intent_routing_config()
                except FileNotFoundError:
                    routing_config = {
                        "intents": {
                            "general": {
                                "target_agent": "",
                                "pre_hooks": [],
                                "post_hooks": [],
                                "keywords": {"en": [], "zh": []},
                            }
                        }
                    }

        llm_classifier_config = routing_config.get("llm_classifier") or {}
        self._min_confidence = llm_classifier_config.get("min_confidence", 0.6)

        # Build routing table from config
        self.routing_table = {}
        intents = routing_config.get("intents") or {}
        for intent_name, intent_cfg in intents.items():
            intent_cfg = intent_cfg or {}
            try:
                category = IntentCategory(intent_name)
            except ValueError:
                continue
            self.routing_table[category] = IntentRoute(
                target_agent=intent_cfg.get("target_agent", ""),
                pre_hooks=intent_cfg.get("pre_hooks", []),
                post_hooks=intent_cfg.get("post_hooks", []),
            )

        # Build classifier chain: Keyword → Semantic(embedding, optional) → LLM.
        keyword_classifier = KeywordIntentClassifier(routing_config)
        chain = keyword_classifier
        llm_router = getattr(self, "llm_router", None)
        embed = getattr(llm_router, "embed", None)
        if embed is not None:
            from harness.core.semantic import SemanticIntentClassifier

            chain.set_next(
                SemanticIntentClassifier(embed, keyword_map=keyword_classifier._keyword_map)
            )
            chain = chain.next_classifier
        if self._llm_text_call:
            llm_classifier = LLMIntentClassifier(self._llm_text_call)
            chain.set_next(llm_classifier)
        self.classifier_chain = keyword_classifier

    def _derive_routing_config_from_registry(self) -> dict | None:
        """Build a routing config from the registry's agent capabilities.

        Each agent's ``AgentCapability`` (name == an IntentCategory value) maps
        that intent to the agent; the capability's keywords feed the classifier.
        Returns None when the registry exposes no capabilities (caller falls
        back to the YAML routing config).
        """
        intents: dict[str, dict] = {}
        found_capabilities = False
        for agent_id, agent in self.agent_registry.get_all_agents().items():
            if agent_id == "master":
                # The master is the orchestrator itself, not a routable target.
                continue
            caps = _agent_capabilities(agent)
            if not caps:
                continue
            found_capabilities = True
            for cap in caps:
                name = getattr(cap, "name", "")
                try:
                    intent = IntentCategory(name)
                except ValueError:
                    continue
                entry = intents.setdefault(
                    intent.value,
                    {
                        "target_agent": agent_id,
                        "pre_hooks": [],
                        "post_hooks": [],
                        "keywords": {"en": [], "zh": []},
                    },
                )
                entry["target_agent"] = agent_id
                for word in getattr(cap, "keywords", None) or []:
                    if not word:
                        continue
                    bucket = "en" if _is_ascii(word) else "zh"
                    if word not in entry["keywords"][bucket]:
                        entry["keywords"][bucket].append(word)
        if not found_capabilities:
            return None
        # Every intent needs a route; unmatched intents fall through to master.
        for intent in IntentCategory:
            intents.setdefault(
                intent.value,
                {
                    "target_agent": "",
                    "pre_hooks": [],
                    "post_hooks": [],
                    "keywords": {"en": [], "zh": []},
                },
            )
        return {"intents": intents}

    @property
    def state(self) -> str:
        return self._state.value if isinstance(self._state, AgentState) else str(self._state)

    def reset_state(self) -> None:
        """Reset orchestrator to IDLE state — allows recovery from ERROR."""
        self._state = AgentState.IDLE
        logger.info("MasterOrchestrator state reset to IDLE")

    async def process(self, message: str, session_id: str) -> str:
        outcome = await self.process_structured(message, session_id)
        return outcome.message

    async def process_structured(self, message: str, session_id: str) -> AgentOutcome:
        """Full orchestration pipeline.

        Args:
            message: User input message.
            session_id: Current session identifier.

        Returns:
            Aggregated response from the target sub-agent.
        """
        self._state = AgentState.PROCESSING
        self.set_state("last_plan", None)

        try:
            events = getattr(getattr(self, "runtime_services", None), "agent_events", None)
            if events:
                await events.record(
                    "agent.master.started",
                    session_id=session_id,
                    agent_id=self.orchestrator_id,
                    step="master_started",
                    status="running",
                    message="Master Agent started orchestration.",
                )

            # 1. Intent classification
            context = {"session_id": session_id}
            classification = await self.classifier_chain.classify(message, context)

            if classification is None:
                classification = IntentClassification(
                    intent=IntentCategory.GENERAL,
                    confidence=0.0,
                    reasoning="No classifier matched",
                )

            logger.info(f"Intent: {classification.intent} (confidence: {classification.confidence:.2f})")
            self.set_state("last_intent", classification.model_dump())
            if events:
                await events.record(
                    "agent.intent.classified",
                    session_id=session_id,
                    agent_id=self.orchestrator_id,
                    step="intent_classification",
                    message=f"Intent classified as {classification.intent}.",
                    metadata=classification.model_dump(),
                )
            await record_audit(
                "agent.intent_classified",
                resource_type="agent",
                resource_id=self.orchestrator_id,
                output_data=classification.model_dump(),
                metadata={
                    "intent": classification.intent,
                    "confidence": classification.confidence,
                },
            )

            await self.event_bus.publish(Event(
                event_type="intent.classified",
                payload=classification.model_dump(),
                session_id=session_id,
            ))

            # 2. Route to sub-agent
            route = self._resolve_route(classification)
            if events:
                await events.record(
                    "agent.routed",
                    session_id=session_id,
                    agent_id=route.target_agent or self.orchestrator_id,
                    step="routing",
                    message=f"Routed to {route.target_agent or 'master'}",
                    metadata={
                        "intent": str(classification.intent),
                        "target_agent": route.target_agent or self.orchestrator_id,
                    },
                )
            await record_audit(
                "agent.routed",
                resource_type="agent",
                resource_id=route.target_agent or self.orchestrator_id,
                decision="allow",
                metadata={
                    "intent": classification.intent,
                    "target_agent": route.target_agent or self.orchestrator_id,
                },
            )
            logger.info(f"Routing → {route.target_agent or 'master (self)'}")

            planner = self._get_planner()
            if planner is not None and (
                should_plan(message) or classification.intent == IntentCategory.PLANNING
            ):
                # The master owns the planning decision AND the DAG execution.
                # The planner (a remote A2A agent, or a local fallback) only
                # *generates* the plan; recovery (suspension / resume) is handled
                # here via the durable PlanExecutor.
                outcome = await planner.process_structured(message, session_id)
                plan_data = (outcome.metadata or {}).get("plan")
                if plan_data is None:
                    # Planner decided this is not plan-worthy — fall through to
                    # normal routing instead of dropping the request.
                    logger.info("Planner declined; routing normally")
                else:
                    from harness.models.plan import ExecutionPlan

                    plan = ExecutionPlan.model_validate(plan_data)
                    plan_result = await self._execute_plan(plan, message, session_id)
                    self.set_state("last_plan", plan.model_dump())
                    self._state = AgentState.IDLE
                    return AgentOutcome(
                        message=plan_result.final_output,
                        metadata={
                            "agent_id": self.orchestrator_id,
                            "session_id": session_id,
                            "intent": classification.model_dump(),
                            "plan": plan.model_dump(),
                        },
                    )

            # 3. Run pre-hooks
            for hook_name in route.pre_hooks:
                await self._run_hook(hook_name, message, session_id)

            # 4. Delegate to target agent (or handle directly for GENERAL)
            if route.target_agent and route.target_agent in self.agent_registry.list_agents():
                target = await self._resolve_target(route.target_agent)
                if hasattr(target, "process_structured"):
                    outcome = await target.process_structured(message, session_id)
                else:
                    response = await target.process(message, session_id)
                    outcome = AgentOutcome(message=response)
            else:
                # Master handles general queries directly via LLM
                if self._llm_text_call:
                    response = await self._llm_text_call(
                        "You are a helpful test automation assistant.",
                        f"User: {message}\n\nProvide a helpful response. If the user is asking about testing, guide them to specify their needs clearly.",
                    )
                else:
                    response = "Master Orchestrator: no LLM configured. Please configure an LLM provider in Settings."
                outcome = AgentOutcome(message=response)

            # 5. Run post-hooks
            for hook_name in route.post_hooks:
                await self._run_hook(hook_name, outcome.message, session_id)

            self._state = AgentState.IDLE
            if events:
                await events.record(
                    "agent.master.completed",
                    session_id=session_id,
                    agent_id=self.orchestrator_id,
                    step="master_completed",
                    message="Master orchestration completed.",
                    metadata={"artifact_count": len(outcome.artifacts)},
                )
            outcome.metadata = {
                **(outcome.metadata or {}),
                "master_agent": self.orchestrator_id,
                "intent": classification.model_dump(),
                "target_agent": route.target_agent or self.orchestrator_id,
            }
            return outcome

        except Exception as e:
            from harness.models.workflow import ExecutionSuspended

            if isinstance(e, ExecutionSuspended):
                self._state = AgentState.WAITING
                raise
            logger.exception("MasterOrchestrator error: %s", e)
            self._state = AgentState.ERROR
            await record_audit(
                "agent.orchestration_failed",
                resource_type="agent",
                resource_id=self.orchestrator_id,
                decision="failure",
                reason=e.__class__.__name__,
            )
            error_msg = f"Orchestration error: {e}"
            return AgentOutcome(
                message=error_msg,
                status="failed",
                metadata={"agent_id": self.orchestrator_id, "session_id": session_id},
            )

    def _resolve_route(self, classification: IntentClassification) -> IntentRoute:
        """Map classified intent to a routing target."""
        return self.routing_table.get(
            classification.intent,
            self.routing_table.get(IntentCategory.GENERAL, IntentRoute(target_agent="")),
        )

    def _get_planner(self):
        """Resolve the planner from the registry-backed remote A2A agent."""
        if "planner" not in self.agent_registry.list_agents():
            return None
        return self.agent_registry.get_agent("planner")

    async def _resolve_target(self, agent_id: str, forced_version: str | None = None):
        """Resolve an agent to its (possibly versioned) remote endpoint.

        When the control-plane ``agent_routing`` table has routing entries for
        this agent, pick a version (weighted, or pinned by ``forced_version``)
        and build an A2A adapter to that endpoint — enabling canary/AB without
        touching the registry. Otherwise fall back to the registry agent.
        """
        registry_agent = self.agent_registry.get_agent(agent_id)
        ws = getattr(getattr(self, "runtime_services", None), "workflows", None)
        db = getattr(ws, "db", None)
        if db is not None:
            from harness.core.agent_router import pick_version

            row = await pick_version(db, agent_id, forced_version)
            if row and row.get("endpoint"):
                from harness.a2a.remote_agent import A2ARemoteAgentAdapter

                card = getattr(registry_agent, "card", None)
                if card is not None:
                    return A2ARemoteAgentAdapter(
                        card=card,
                        endpoint=row["endpoint"],
                        token=getattr(registry_agent, "token", ""),
                    )
        return registry_agent

    async def _execute_plan(self, plan, message: str, session_id: str):
        """Drive DAG execution (master-owned), with durable suspension support.

        The plan came from the planner (generation only). Execution runs here so
        the master holds the workflow state; on a node's tool suspension the
        plan is checkpointed and an approved resume continues the remaining DAG.
        """
        from harness.core.execution import PlanExecutor

        workflows = getattr(getattr(self, "runtime_services", None), "workflows", None)
        executor = PlanExecutor(self.agent_registry, workflow_service=workflows)
        return await executor.execute(plan, session_id=session_id)

    async def submit_plan(self, message: str, session_id: str) -> dict:
        """Stateless submission: classify + plan + create a durable plan-run.

        Returns ``{"run_id", "status"}`` without blocking; a ``WorkflowDriver``
        (multi-replica safe) advances the DAG from the control-plane database.
        Falls back to a synchronous outcome when the message is not plan-worthy.
        """
        from harness.models.artifact import AgentOutcome

        workflows = getattr(getattr(self, "runtime_services", None), "workflows", None)
        if workflows is None:
            raise RuntimeError("Runtime workflows service is unavailable")

        classification = await self.classifier_chain.classify(
            message, {"session_id": session_id}
        )
        if classification is None:
            classification = IntentClassification(
                intent=IntentCategory.GENERAL, confidence=0.0, reasoning="no match"
            )
        planner = self._get_planner()
        if planner is not None and (
            should_plan(message) or classification.intent == IntentCategory.PLANNING
        ):
            outcome = await planner.process_structured(message, session_id)
            plan_data = (outcome.metadata or {}).get("plan")
            if plan_data is not None:
                from harness.models.plan import ExecutionPlan

                plan = ExecutionPlan.model_validate(plan_data)
                workflow_id = await workflows.create_run(
                    session_id=session_id,
                    input_data={"message": message, "kind": "plan_run"},
                    agent_id="master",
                )
                token = set_execution_context(
                    get_execution_context().child(workflow_id=workflow_id)
                )
                try:
                    await workflows.update_output_data(
                        workflow_id,
                        {
                            "kind": "plan_run",
                            "plan": plan.model_dump(),
                            "node_outputs": {},
                            "done_nodes": [],
                            "session_id": session_id,
                            "objective": message,
                        },
                    )
                finally:
                    reset_execution_context(token)
                self.set_state("last_plan", plan.model_dump())
                return {"run_id": workflow_id, "status": "running"}

        # Not plan-worthy → synchronous direct handling (no DAG).
        route = self._resolve_route(classification)
        if route.target_agent and route.target_agent in self.agent_registry.list_agents():
            target = await self._resolve_target(route.target_agent)
            if hasattr(target, "process_structured"):
                outcome = await target.process_structured(message, session_id)
            else:
                outcome = AgentOutcome(message=await target.process(message, session_id))
        elif self._llm_text_call:
            response = await self._llm_text_call(
                "You are a helpful test automation assistant.",
                f"User: {message}\n\nProvide a helpful response.",
            )
            outcome = AgentOutcome(message=response)
        else:
            outcome = AgentOutcome(message="Master: no LLM configured")
        return {"run_id": None, "status": outcome.status, "message": outcome.message}

    async def _run_hook(self, hook_name: str, data: str, session_id: str) -> None:
        """Execute a named hook.

        Args:
            hook_name: Hook identifier.
            data: Data to pass to the hook.
            session_id: Current session.
        """
        logger.debug(f"Running hook: {hook_name} for session {session_id}")
        # Hook implementations can be registered and executed here

    def register_routing_rule(
        self,
        intent: IntentCategory,
        target_agent: str,
        pre_hooks: list[str] | None = None,
        post_hooks: list[str] | None = None,
    ) -> None:
        """Dynamically register or update a routing rule at runtime.

        Args:
            intent: The intent category.
            target_agent: Agent ID to route to.
            pre_hooks: Optional list of pre-hook names.
            post_hooks: Optional list of post-hook names.
        """
        self.routing_table[intent] = IntentRoute(
            target_agent=target_agent,
            pre_hooks=pre_hooks or [],
            post_hooks=post_hooks or [],
        )
        logger.info(f"Routing rule registered: {intent.value} → {target_agent}")


# ─── Backward-compatibility alias ─────────────────────────────
# MasterAgent is kept as a thin wrapper for code that still imports it.
# New code should use MasterOrchestrator directly.

    def set_state(self, key: str, value: Any) -> None:
        self._runtime_state[key] = value

    def get_state(self, key: str, default: Any = None) -> Any:
        return self._runtime_state.get(key, default)


class MasterAgent(MasterOrchestrator):
    """Backward-compatible wrapper around MasterOrchestrator.

    Deprecated: Use MasterOrchestrator directly for new code.
    This class exists so existing wire_agents code doesn't break.
    """

    agent_name: str = "Master Orchestrator"
    agent_id: str = "master"

    def __init__(
        self,
        config: AgentConfig,
        llm_router,
        agent_registry: AgentRegistry,
        event_bus: EventBus,
    ) -> None:
        # Build a callable for LLM text from the router (adapts LLMResponse → str)
        async def _llm_text_call(system_prompt: str, user_message: str) -> str:
            from harness.llm.types import LLMRequest
            request = LLMRequest(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=0.2,
                max_tokens=4096,
            )
            response = await llm_router.complete(request)
            return response.content

        super().__init__(
            agent_registry=agent_registry,
            event_bus=event_bus,
            llm_text_call=_llm_text_call,
        )

        # Store config for backward compatibility
        self.config = config
        self.llm_router = llm_router
        self.turn_count = 0

        # Wire the state property to match old interface
        AgentState # noqa: B018 — ensure import is used

    async def _on_before_process(self, message: str, session_id: str) -> None:
        pass

    async def _on_after_process(self, message: str, result: str, session_id: str) -> None:
        pass

    def get_context_window(self) -> list:
        return []

    def add_turn(self, role: str, content: str, metadata: dict | None = None) -> None:
        self.turn_count += 1
