"""Wire up agents from YAML config with LLM providers.

This module creates agent instances from their config files,
connecting them to LLM providers and registering them in the AgentRegistry.
"""

import logging
import os
from pathlib import Path

from harness.api.config_store import ConfigStore
from harness.core.events import EventBus
from harness.core.master import MasterAgent
from harness.core.registry import AgentRegistry
from harness.llm.router import ModelRouter
from harness.models.agent import (
    AgentConfig,
    ContextConfig,
    LLMConfig,
    MemoryConfig,
    VectorConfig,
)
from harness.runtime.services import RuntimeServices
from harness.skills.registry import SkillRegistry
from harness.utils.config import load_all_agent_configs

logger = logging.getLogger(__name__)


class DemoLLMProvider:
    """Demo LLM provider that returns structured responses without real API calls.

    Used when no API keys are configured. Provides enough functionality
    to demonstrate intent classification and routing.
    """

    provider_name = "demo"
    default_model = "demo"

    async def complete(self, request):
        from harness.llm.types import LLMResponse

        # Try to extract just the user message from classification prompts
        msg = request.user_message
        if "User message:" in msg:
            idx = msg.rfind("User message:")
            msg = msg[idx + len("User message:"):]
        msg = msg.strip().lower()

        # Simple keyword matching for demo — log analysis checked FIRST
        if any(kw in msg for kw in ["日志", "log", "诊断", "diagnos", "失败", "报错", "错误", "error", "分析", "analyze", "failure", "fail", "trace", "root cause", "debug", "崩溃", "crash", "timeout", "超时"]):
            return LLMResponse(content='{"intent": "log_analysis", "confidence": 0.90, "reasoning": "Keyword match: log-analysis-related terms"}')
        elif any(kw in msg for kw in ["需求", "requirement", "prd", "spec", "用户故事"]):
            return LLMResponse(content='{"intent": "requirements_analysis", "confidence": 0.85, "reasoning": "Keyword match: requirements-related terms"}')
        elif any(kw in msg for kw in ["用例", "test case", "测试用例"]):
            return LLMResponse(content='{"intent": "test_case_generation", "confidence": 0.85, "reasoning": "Keyword match: test-case-related terms"}')
        elif any(kw in msg for kw in ["脚本", "script", "pytest", "selenium", "playwright", "自动化", "代码"]):
            return LLMResponse(content='{"intent": "script_generation", "confidence": 0.85, "reasoning": "Keyword match: script-generation-related terms"}')
        elif any(kw in msg for kw in ["执行", "运行", "run", "schedule", "跑"]):
            return LLMResponse(content='{"intent": "schedule_execution", "confidence": 0.85, "reasoning": "Keyword match: execution-related terms"}')
        else:
            return LLMResponse(content='{"intent": "general", "confidence": 0.5, "reasoning": "No strong keyword match"}')

    async def stream(self, request):
        from harness.llm.types import LLMStreamChunk
        yield LLMStreamChunk(content_delta="Demo streaming not supported")
        yield LLMStreamChunk(finish_reason="stop")

    async def embed(self, texts, model=None):
        return [[0.0] * 384 for _ in texts]

    def count_tokens(self, text, model=None):
        return len(text) // 4


def _api_keys_from_config() -> dict[str, str]:
    """Read API keys from config/default.yaml's llm.api_keys (env-interpolated).

    Keys are resolved as ``${ENV_VAR}`` references when present, otherwise the
    literal value is used. This lets a deployment pin keys in the config file
    instead of relying solely on process environment variables.
    """
    try:
        from harness.utils.config import load_yaml_config

        cfg = load_yaml_config(Path("config/default.yaml"))
        keys = (cfg.get("llm") or {}).get("api_keys") or {}
        # Drop unresolved ${...} placeholders (e.g. "${ANTHROPIC_API_KEY}") and
        # empty values — only a real pinned key in the file is used.
        return {
            k: str(v)
            for k, v in keys.items()
            if v and "${" not in str(v)
        }
    except Exception:
        return {}


async def create_llm_router(llm_config: LLMConfig, agent_id: str = "", config_store: ConfigStore | None = None) -> ModelRouter:
    """Create a ModelRouter from LLM config, reading API keys from ConfigStore.

    Merge strategy (highest priority first):
    1. Per-agent LLM override (by agent_id in agent_llm_overrides)
    2. Environment variables
    3. Saved provider matching (prefers base_url match, then explicit default, then first available)
    4. Fallback: DemoLLMProvider

    Args:
        llm_config: LLM configuration from AgentConfig.
        agent_id: Optional agent ID for per-agent overrides.
        config_store: Optional ConfigStore instance. If None, creates a temporary one.

    Returns:
        A ModelRouter instance.
    """
    if config_store is None:
        # Fallback: create a temporary store (shouldn't happen in normal flow)
        from harness.db import create_database
        from harness.runtime.settings import RuntimeSettings

        db = create_database(RuntimeSettings().database_url)
        await db.initialize()
        config_store = ConfigStore(db)

    saved_providers_list = await config_store.get_llm_providers()
    saved_providers = {p["name"]: p for p in saved_providers_list}

    # API keys: environment variable first, then config/default.yaml's llm.api_keys.
    file_keys = _api_keys_from_config()
    env_keys = {
        "anthropic": os.environ.get("ANTHROPIC_API_KEY", "") or file_keys.get("anthropic", ""),
        "openai": os.environ.get("OPENAI_API_KEY", "") or file_keys.get("openai", ""),
    }

    # Resolved values (start with agent YAML defaults)
    api_key = ""
    base_url = ""
    actual_provider = llm_config.provider
    resolved_model = llm_config.model
    resolved_max_tokens = llm_config.max_tokens
    resolved_temperature = llm_config.temperature
    matched = None

    # Step 1: Check per-agent LLM override (agent-specific binding)
    agent_overrides = await config_store.get("agent_llm_overrides", agent_id) or {}
    # Support both "api_key_name" (UI) and "provider_name" (YAML) naming conventions
    api_key_name = (
        agent_overrides.get("api_key_name")
        or agent_overrides.get("provider_name")
        or agent_overrides.get("api_key")
        or ""
    )

    if api_key_name and api_key_name in saved_providers:
        p = saved_providers[api_key_name]
        api_key = p.get("api_key", "")
        base_url = p.get("base_url", "") or base_url
        actual_provider = p.get("provider", llm_config.provider)
        if p.get("model"):
            resolved_model = p["model"]
        resolved_max_tokens = p.get("max_tokens", resolved_max_tokens)
        resolved_temperature = p.get("temperature", resolved_temperature)
        logger.info(f"Agent '{agent_id}' → per-agent override → provider '{api_key_name}' ({actual_provider} model={resolved_model})")
    elif llm_config.provider in env_keys and env_keys[llm_config.provider]:
        # Step 2: Environment variable fallback
        api_key = env_keys[llm_config.provider]
    else:
        # Step 3: Smart auto-match from saved providers.
        candidates = [
            p for p in saved_providers_list
            if p.get("provider") == llm_config.provider and p.get("api_key")
        ]
        default_entry = await config_store.get("llm_settings", "default_provider")
        default_name = ""
        if default_entry:
            default_name = default_entry.get("value", "") if isinstance(default_entry, dict) else str(default_entry)

        candidates_with_url = [p for p in candidates if p.get("base_url")]

        if candidates:
            # a) Per-agent LLM provider binding
            if not matched and agent_id and agent_overrides:
                bound_name = agent_overrides.get("api_key_name") or agent_overrides.get("provider_name") or ""
                if bound_name and bound_name in saved_providers:
                    matched = saved_providers[bound_name]
                    logger.info(f"Agent '{agent_id}' bound to provider '{bound_name}'")

            # b) Agent's YAML config llm.provider_name field
            if not matched:
                explicit_name = getattr(llm_config, "provider_name", "") or ""
                if explicit_name and explicit_name in saved_providers:
                    matched = saved_providers[explicit_name]
                    logger.info(f"Agent '{agent_id}' matched by YAML provider_name={explicit_name}")

            # c) Explicit default provider by name
            if not matched and default_name:
                matched = next((p for p in candidates if p.get("name") == default_name), None)

            # d) Provider whose model matches the agent's configured model
            if not matched:
                matched = next((p for p in candidates if p.get("model") == llm_config.model), None)

            # e) Prefer providers with base_url
            if not matched:
                if candidates_with_url:
                    matched = candidates_with_url[0]
                else:
                    matched = candidates[0]

            # f) Any matching provider type (fallback)
            if not matched:
                matched = candidates[0]
        elif saved_providers_list:
            with_url = [p for p in saved_providers_list if p.get("base_url") and p.get("api_key")]
            matched = with_url[0] if with_url else saved_providers_list[0]

        if matched:
            api_key = matched.get("api_key", "")
            base_url = matched.get("base_url", "") or base_url
            actual_provider = matched.get("provider", actual_provider)
            if matched.get("model"):
                resolved_model = matched["model"]
            resolved_max_tokens = matched.get("max_tokens", resolved_max_tokens)
            resolved_temperature = matched.get("temperature", resolved_temperature)

    # Build the router
    if api_key:
        from harness.llm.factory import LLMProviderFactory
        effective_key_name = api_key_name or (matched.get("name", "") if matched else "environment")
        logger.info(
            f"Agent '{agent_id}' → LLM: provider={actual_provider} "
            f"model={resolved_model} max_tokens={resolved_max_tokens} "
            f"base_url={base_url!r} key_name={api_key_name or 'auto-detected'}"
        )
        primary = LLMProviderFactory.create(
            actual_provider, api_key=api_key,
            default_model=resolved_model,
            base_url=base_url if base_url else None,
        )
        router = ModelRouter(primary=primary)
    else:
        logger.warning(f"No API key found for '{llm_config.provider}', using DemoLLMProvider")
        router = ModelRouter(primary=DemoLLMProvider())
        effective_key_name = "demo"

    router.effective_config = {
        "provider": router.primary.provider_name,
        "model": router.primary.default_model,
        "provider_name": effective_key_name,
        "base_url": base_url,
        "temperature": resolved_temperature,
        "max_tokens": resolved_max_tokens,
    }

    return router


async def wire_all_agents(
    registry: AgentRegistry,
    event_bus: EventBus,
    skill_registry: SkillRegistry,
    config_store: ConfigStore | None = None,
    runtime_services: RuntimeServices | None = None,
    config_dir: str = "config/agents",
) -> MasterAgent:
    """Create and register all agents from configuration.

    Args:
        registry: AgentRegistry to register agents in.
        event_bus: EventBus for agent events.
        skill_registry: SkillRegistry for skill binding.
        config_store: ConfigStore for reading LLM provider configs.
        config_dir: Directory containing agent YAML configs.

    Returns:
        The MasterAgent instance.
    """
    raw_configs = load_all_agent_configs(config_dir)
    master_agent = None

    for agent_id, raw in raw_configs.items():
        if agent_id != "master":
            # 专业子 agent 已迁移到独立的远程 harness-agents 服务（A2A），
            # 由 import_remote_agents_from_endpoint 导入注册，本地不再创建。
            continue
        llm_cfg = raw.get("llm", {})
        llm_config = LLMConfig(
            provider=llm_cfg.get("provider", "anthropic"),
            model=llm_cfg.get("model", "claude-sonnet-4-20250514"),
            temperature=llm_cfg.get("temperature", 0.1),
            max_tokens=llm_cfg.get("max_tokens", 4096),
            fallback_providers=llm_cfg.get("fallback_providers", []),
            provider_name=llm_cfg.get("provider_name", ""),
        )

        vector_cfg = raw.get("vector", {})
        vector_config = VectorConfig(
            collection_name=vector_cfg.get("collection_name", agent_id),
            embedding_model=vector_cfg.get("embedding_model", "text-embedding-3-small"),
            embedding_provider=vector_cfg.get("embedding_provider", "openai"),
            persist_directory=vector_cfg.get("persist_directory", "data/chroma"),
        )

        memory_cfg = raw.get("memory", {})
        memory_config = MemoryConfig(
            storage_path=memory_cfg.get("storage_path", f"data/memory/{agent_id}"),
            short_term_ttl_seconds=memory_cfg.get("short_term_ttl_seconds", 3600),
            long_term_enabled=memory_cfg.get("long_term_enabled", True),
        )

        context_cfg = raw.get("context", {})
        context_config = ContextConfig(
            max_turns=context_cfg.get("max_turns", 20),
            include_summary=context_cfg.get("include_summary", True),
        )

        # Build AgentConfig
        agent_config = AgentConfig(
            id=agent_id,
            name=raw.get("name", agent_id),
            description=raw.get("description", ""),
            execution_mode=raw.get("execution_mode", "plan"),
            react_max_iterations=raw.get("react_max_iterations", 15),
            llm=llm_config,
            context=context_config,
            memory=memory_config,
            vector=vector_config,
            knowledge_bases=raw.get("knowledge_bases", []),
            tools=raw.get("tools", []),
            bind_skills=raw.get("bind_skills", []),
            mcp_servers=[],
            prompts=raw.get("prompts", {}),
            min_confidence=raw.get("min_confidence", 0.6),
        )

        # Create LLM router (pass agent_id for per-agent provider resolution)
        llm_router = await create_llm_router(llm_config, agent_id=agent_id, config_store=config_store)

        # Create agent instance
        if agent_id == "master":
            master_agent = MasterAgent(
                config=agent_config,
                llm_router=llm_router,
                agent_registry=registry,
                event_bus=event_bus,
            )
            master_agent.runtime_services = runtime_services
            registry.set_master_agent(master_agent)
        else:
            # Specialized agents are remote A2A services and are imported
            # after startup by _import_configured_remote_a2a_agents.
            continue

    if master_agent is None:
        # Create a minimal master agent if none configured
        logger.warning("No master agent config found, creating minimal master")
        minimal_config = AgentConfig(
            id="master",
            name="Master Orchestrator",
            description="Auto-created master agent",
            execution_mode="plan",
            llm=LLMConfig(provider="anthropic", model="claude-sonnet-4-20250514"),
            vector=VectorConfig(collection_name="master"),
        )
        llm_router = ModelRouter(primary=DemoLLMProvider())
        master_agent = MasterAgent(
            config=minimal_config,
            llm_router=llm_router,
            agent_registry=registry,
            event_bus=event_bus,
        )
        master_agent.runtime_services = runtime_services
        registry.set_master_agent(master_agent)

    # The planner is now an independent remote agent (harness-agents project),
    # imported via A2A into the registry. The master resolves it from the
    # registry; no local planner is constructed here.
    logger.info(f"Wired {len(registry.list_agents())} agent(s): {registry.list_agents()}")
    return master_agent
