"""LLM provider factory — creates providers from configuration.

Uses lazy imports so the app can start without all provider packages installed.
"""

from harness.llm.base import AbstractLLMProvider


class LLMProviderFactory:
    """Factory for creating LLM provider instances from configuration.

    Supports runtime registration of custom providers.
    Provider classes are imported lazily — only when first requested.
    """

    _registry: dict[str, str] = {
        "anthropic": "harness.llm.providers.anthropic.AnthropicProvider",
        "openai": "harness.llm.providers.openai.OpenAIProvider",
        # OpenAI-compatible providers (all use the same OpenAI client with custom base_url)
        "qwen": "harness.llm.providers.openai.OpenAIProvider",
        "deepseek": "harness.llm.providers.openai.OpenAIProvider",
        "zhipu": "harness.llm.providers.openai.OpenAIProvider",
    }
    _cache: dict[str, type[AbstractLLMProvider]] = {}

    @classmethod
    def register(cls, name: str, provider_path: str) -> None:
        """Register a custom LLM provider by dotted import path.

        Args:
            name: Provider name (e.g. "anthropic").
            provider_path: Dotted path to the provider class.
        """
        cls._registry[name] = provider_path
        cls._cache.pop(name, None)  # Clear cache entry

    @classmethod
    def create(cls, provider_name: str, api_key: str, default_model: str | None = None, base_url: str | None = None) -> AbstractLLMProvider:
        """Create a provider instance by name.

        Args:
            provider_name: e.g. "anthropic", "openai", "demo".
            api_key: API key for the provider.
            default_model: Override the default model.
            base_url: Custom API base URL (for OpenAI-compatible providers like Qwen/DeepSeek).

        Returns:
            An AbstractLLMProvider instance.

        Raises:
            ValueError: If the provider name is unknown.
        """
        if provider_name == "demo":
            # Demo provider doesn't need importing
            from harness.api.wire_agents import DemoLLMProvider
            return DemoLLMProvider()

        provider_path = cls._registry.get(provider_name)
        if provider_path is None:
            available = list(cls._registry.keys()) + ["demo"]
            raise ValueError(f"Unknown LLM provider: {provider_name}. Available: {available}")

        # Lazy import
        if provider_name not in cls._cache:
            try:
                module_path, class_name = provider_path.rsplit(".", 1)
                import importlib
                module = importlib.import_module(module_path)
                cls._cache[provider_name] = getattr(module, class_name)
            except ImportError as e:
                raise ImportError(
                    f"Cannot import provider '{provider_name}'. "
                    f"Install the required package: pip install {provider_name}"
                ) from e

        provider_cls = cls._cache[provider_name]
        kwargs = {"api_key": api_key}
        if default_model:
            kwargs["default_model"] = default_model
        if base_url:
            kwargs["base_url"] = base_url
        return provider_cls(**kwargs)

    @classmethod
    def list_providers(cls) -> list[str]:
        """List all registered provider names."""
        return list(cls._registry.keys()) + ["demo"]
