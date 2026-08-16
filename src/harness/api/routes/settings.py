"""Settings API routes — LLM provider and system configuration."""

import json as _json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from harness.api.config_store import ConfigStore
from harness.api.deps import get_agent_registry, get_config_store
from harness.api.wire_agents import create_llm_router
from harness.core.registry import AgentRegistry
from harness.llm.factory import LLMProviderFactory
from harness.observability.audit import record_audit
from harness.security.dependencies import require_permission
from harness.security.models import ActorContext

logger = logging.getLogger(__name__)
router = APIRouter()

DEFAULT_QA_ROUTING_CONFIG = {
    "classifier_endpoint": "http://127.0.0.1:8101/a2a",
    "classifier_agent_id": "remote.question_classifier",
    "qa_agent_id": "remote.qa_conversation",
    "threshold": 0.72,
    "intent_mappings": {
        "general_qa": "remote.qa_conversation",
        "requirements": "remote.requirements",
        "test_case": "remote.test_cases",
        "script": "remote.scripts",
        "execution": "remote.execution",
        "diagnosis": "remote.diagnosis",
        "unknown": "master",
    },
    "artifact_policy": "explicit_save_or_structured_report",
    "operations_tools_enabled": True,
    "synchronous_qa": True,
}


# ─── LLM Provider Management ───────────────────────────────────

@router.get("/llm")
async def list_llm_providers(
    store: ConfigStore = Depends(get_config_store),
    _: ActorContext = Depends(require_permission("config:read")),
):
    """List all configured LLM providers (API keys masked)."""
    providers = await store.get_llm_providers()
    # Mask API keys
    masked = []
    for p in providers:
        p = dict(p)
        key = p.get("api_key", "")
        if key and len(key) > 8:
            p["api_key"] = key[:4] + "****" + key[-4:]
        elif key:
            p["api_key"] = "****"
        masked.append(p)
    return {"providers": masked, "count": len(masked)}


@router.post("/llm")
async def save_llm_provider(
    body: dict,
    store: ConfigStore = Depends(get_config_store),
    registry: AgentRegistry = Depends(get_agent_registry),
    _: ActorContext = Depends(require_permission("config:write")),
):
    """Add or update an LLM provider configuration.

    Body: {name, provider, api_key, model, base_url?, protocol?, provider_label?}
    """
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Provider 'name' is required")

    # provider = protocol (which SDK), provider_label = display name
    # Store base_url exactly as provided; _normalize_base_url() handles SDK formatting
    config = {
        "name": name,
        "provider": body.get("provider", "anthropic"),
        "api_key": body.get("api_key", "").strip(),
        "model": body.get("model", "").strip(),
        "base_url": body.get("base_url", "").strip(),
        "provider_label": body.get("provider_label", body.get("provider", "anthropic")),
        "temperature": body.get("temperature", 0.2),
        "max_tokens": body.get("max_tokens", 4096),
    }
    previous = await store.get_llm_provider(name)
    await store.save_llm_provider(name, config)
    await record_audit(
        "config.llm.updated" if previous else "config.llm.created",
        resource_type="llm_provider",
        resource_id=name,
        input_data={"previous": previous, "current": config},
        metadata={"provider": config["provider"], "model": config["model"]},
    )
    logger.info(f"LLM provider '{name}' ({config['provider']}) saved — base_url={config['base_url']!r}")
    rebound_agents = await _rebind_local_agent_llms(registry, store)
    return {"message": f"Provider '{name}' saved", "name": name, "rebound_agents": rebound_agents}


async def _rebind_local_agent_llms(registry: AgentRegistry, store: ConfigStore) -> list[str]:
    rebound = []
    for agent_id, agent in registry.get_all_agents().items():
        config = getattr(agent, "config", None)
        if config is None or not hasattr(agent, "llm_router"):
            continue
        agent.llm_router = await create_llm_router(config.llm, agent_id=agent_id, config_store=store)
        rebound.append(agent_id)
    return rebound


@router.delete("/llm/{name}")
async def delete_llm_provider(
    name: str,
    store: ConfigStore = Depends(get_config_store),
    _: ActorContext = Depends(require_permission("config:write")),
):
    """Delete an LLM provider configuration."""
    previous = await store.get_llm_provider(name)
    if await store.delete_llm_provider(name):
        await record_audit(
            "config.llm.deleted",
            resource_type="llm_provider",
            resource_id=name,
            input_data=previous,
        )
        return {"message": f"Provider '{name}' deleted"}
    raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")


@router.post("/llm/test")
async def test_llm_connection(
    body: dict,
    store: ConfigStore = Depends(get_config_store),
    _: ActorContext = Depends(require_permission("config:write")),
):
    """Test connection to an LLM provider.

    Accepts either:
    - provider_name: look up the saved provider by name (from settings page list)
    - provider + api_key + model + base_url: manual test (from settings page form)
    """
    provider_name = (body.get("provider_name") or "").strip()
    provider_type = (body.get("provider") or "anthropic").strip()
    api_key = (body.get("api_key") or "").strip()
    model = (body.get("model") or "").strip()
    base_url = (body.get("base_url") or "").strip()

    # If provider_name is given, look up the real config from ConfigStore
    if provider_name:
        saved = await store.get_llm_provider(provider_name)
        logger.info(f"LLM test lookup: provider_name={provider_name!r} found={saved is not None}")
        if saved:
            provider_type = saved.get("provider", "anthropic")
            api_key = (saved.get("api_key") or "").strip()
            model = (saved.get("model") or "").strip()
            base_url = (saved.get("base_url") or "").strip()
            logger.info(f"LLM test resolved: provider={provider_type} model={model} base_url={base_url!r} api_key_len={len(api_key)}")
        else:
            return {"success": False, "error": f"Provider '{provider_name}' not found"}
    else:
        logger.info(
            "LLM test: provider=%s model=%s base_url=%s api_key_configured=%s",
            provider_type,
            model,
            base_url,
            bool(api_key),
        )

    if not api_key:
        return {"success": False, "error": "API key is required"}

    try:
        from harness.llm.types import LLMRequest
        from harness.llm.providers.anthropic import _normalize_base_url

        # Log the final URL the SDK will use
        actual_base = base_url
        if provider_type == "anthropic" and actual_base:
            norm = _normalize_base_url(actual_base)
            logger.info(f"LLM test Anthropic URL: user={actual_base!r} norm={norm!r} final={norm + '/messages'!r}")
        elif actual_base:
            logger.info(f"LLM test OpenAI URL: user={actual_base!r}")

        llm = LLMProviderFactory.create(
            provider_type, api_key=api_key, default_model=model or None,
            base_url=base_url if base_url else None,
        )
        test_req = LLMRequest(
            system_prompt="You are a helpful assistant. Reply with just 'OK'.",
            user_message="Ping",
            max_tokens=10,
        )
        resp = await llm.complete(test_req)
        await record_audit(
            "config.llm.tested",
            resource_type="llm_provider",
            resource_id=provider_name or provider_type,
            decision="success",
            metadata={"provider": provider_type, "model": resp.model or model},
        )
        return {"success": True, "message": f"Connection OK — model: {resp.model}", "response": resp.content[:100]}
    except Exception as e:
        import traceback
        full_error = f"{e.__class__.__name__}: {e}"
        logger.warning(f"LLM connection test failed: provider={provider_type} model={model} base_url={actual_base!r} error={e}\n{traceback.format_exc()}")
        await record_audit(
            "config.llm.tested",
            resource_type="llm_provider",
            resource_id=provider_name or provider_type,
            decision="failure",
            reason=e.__class__.__name__,
            metadata={"provider": provider_type, "model": model},
        )
        return {"success": False, "error": full_error}


# ─── System Info ───────────────────────────────────────────────

@router.get("/system")
async def system_info(
    store: ConfigStore = Depends(get_config_store),
    _: ActorContext = Depends(require_permission("config:read")),
):
    """Get system configuration summary."""
    llm_count = len(await store.get_llm_providers())
    mcp_count = len(await store.get_mcp_servers())
    return {
        "llm_providers_configured": llm_count,
        "mcp_servers_configured": mcp_count,
        "demo_mode": llm_count == 0,
    }


@router.get("/qa-routing")
async def get_qa_routing_config(
    store: ConfigStore = Depends(get_config_store),
    _: ActorContext = Depends(require_permission("config:read")),
):
    # Deprecated: QA/Classifier Routing removed. Remote A2A services are managed
    # under Agent management (remote-services). Kept as a 410 to signal removal.
    raise HTTPException(status_code=410, detail="QA/Classifier Routing was removed; use Agent management remote-services")

