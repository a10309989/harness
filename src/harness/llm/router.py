"""Model router with automatic provider fallback (Chain of Responsibility)."""

import asyncio
import logging

from harness.llm.base import AbstractLLMProvider, LLMAllProvidersFailedError
from harness.llm.types import LLMRequest, LLMResponse
from harness.models.agent import LLMConfig
from harness.observability.audit import record_audit
from harness.observability.context import child_span

logger = logging.getLogger(__name__)


class ModelRouter:
    """Routes LLM requests with automatic fallback across providers.

    Tries the primary provider first; on failure, falls through the
    fallback chain. Implements the Chain of Responsibility pattern.
    """

    def __init__(
        self,
        primary: AbstractLLMProvider,
        fallbacks: list[AbstractLLMProvider] | None = None,
    ) -> None:
        self.primary = primary
        self.fallbacks = fallbacks or []

    @classmethod
    def from_config(cls, config: LLMConfig, api_keys: dict[str, str]) -> "ModelRouter":
        """Build a ModelRouter from LLMConfig.

        Args:
            config: LLM configuration from AgentConfig.
            api_keys: dict mapping provider name to API key.

        Returns:
            A configured ModelRouter instance.
        """
        from harness.llm.factory import LLMProviderFactory

        primary = LLMProviderFactory.create(
            provider_name=config.provider,
            api_key=api_keys.get(config.provider, ""),
            default_model=config.model,
        )

        fallbacks = []
        for fb_name in config.fallback_providers:
            try:
                fb = LLMProviderFactory.create(
                    provider_name=fb_name,
                    api_key=api_keys.get(fb_name, ""),
                )
                fallbacks.append(fb)
            except Exception as e:
                logger.warning(f"Failed to initialize fallback provider '{fb_name}': {e}")

        return cls(primary=primary, fallbacks=fallbacks)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Try primary provider, fall back through the chain on failure."""
        providers = [self.primary] + self.fallbacks
        last_error: Exception | None = None

        for i, provider in enumerate(providers):
            with child_span():
                await record_audit(
                    "llm.requested",
                    resource_type="llm_provider",
                    resource_id=provider.provider_name,
                    input_data={
                        "system_prompt": request.system_prompt,
                        "user_message": request.user_message,
                        "messages": request.messages,
                    },
                    metadata={
                        "provider": provider.provider_name,
                        "model": request.metadata.get("model", provider.default_model),
                        "attempt": i + 1,
                        "max_tokens": request.max_tokens,
                    },
                )
                try:
                    response = await provider.complete(request)
                    if i > 0:
                        logger.info(f"Fallback to {provider.provider_name} succeeded")
                    await record_audit(
                        "llm.completed",
                        resource_type="llm_provider",
                        resource_id=provider.provider_name,
                        output_data={
                            "content": response.content,
                            "tool_calls": response.tool_calls,
                        },
                        metadata={
                            "provider": provider.provider_name,
                            "model": response.model or provider.default_model,
                            "finish_reason": response.finish_reason,
                            "input_tokens": response.usage.input_tokens if response.usage else 0,
                            "output_tokens": response.usage.output_tokens if response.usage else 0,
                            "fallback": i > 0,
                        },
                    )
                    return response
                except Exception as e:
                    logger.warning(f"Provider '{provider.provider_name}' failed: {e}")
                    await record_audit(
                        "llm.failed",
                        resource_type="llm_provider",
                        resource_id=provider.provider_name,
                        decision="failure",
                        reason=e.__class__.__name__,
                        metadata={
                            "attempt": i + 1,
                            "fallback_available": i < len(providers) - 1,
                        },
                    )
                    last_error = e
                    if i < len(providers) - 1:
                        await asyncio.sleep(0.5)

        raise LLMAllProvidersFailedError(
            f"All {len(providers)} provider(s) failed. Last error: {last_error}"
        )

    @property
    def active_provider(self) -> str:
        """Return the name of the primary provider."""
        return self.primary.provider_name
