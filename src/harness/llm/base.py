"""Abstract LLM provider interface (Strategy pattern)."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from harness.llm.types import LLMRequest, LLMResponse, LLMStreamChunk


class AbstractLLMProvider(ABC):
    """Strategy interface for LLM providers.

    Each provider (Anthropic, OpenAI, local) implements this interface,
    enabling seamless provider switching and fallback chains.
    """

    provider_name: str
    default_model: str

    def __init__(self, api_key: str, default_model: str | None = None) -> None:
        self.api_key = api_key
        if default_model:
            self.default_model = default_model

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Send a completion request and get the full response."""
        ...

    @abstractmethod
    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        """Stream completion tokens as they arrive."""
        ...

    @abstractmethod
    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        """Generate embeddings for the given texts."""
        ...

    @abstractmethod
    def count_tokens(self, text: str, model: str | None = None) -> int:
        """Estimate token count for a text string."""
        ...


class LLMAllProvidersFailedError(Exception):
    """Raised when all LLM providers (primary + fallbacks) have failed."""

    pass
