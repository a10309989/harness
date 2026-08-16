"""Unit tests for LLM router, provider factory, and model router fallback."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from harness.llm.router import ModelRouter
from harness.llm.factory import LLMProviderFactory
from harness.llm.types import LLMRequest, LLMResponse, normalize_provider_messages


# ─── ModelRouter ──────────────────────────────────────────────

class TestModelRouter:
    @pytest.fixture
    def mock_primary(self):
        provider = MagicMock()
        provider.provider_name = "test_primary"
        provider.complete = AsyncMock(return_value=LLMResponse(content="primary response"))
        return provider

    @pytest.fixture
    def mock_fallback(self):
        provider = MagicMock()
        provider.provider_name = "test_fallback"
        provider.complete = AsyncMock(return_value=LLMResponse(content="fallback response"))
        return provider

    async def test_primary_succeeds(self, mock_primary, mock_fallback):
        router = ModelRouter(primary=mock_primary, fallbacks=[mock_fallback])
        request = LLMRequest(system_prompt="sys", user_message="hello")
        response = await router.complete(request)
        assert response.content == "primary response"
        mock_primary.complete.assert_called_once()
        mock_fallback.complete.assert_not_called()

    async def test_primary_fails_fallback_succeeds(self, mock_primary, mock_fallback):
        mock_primary.complete = AsyncMock(side_effect=Exception("Primary down"))
        router = ModelRouter(primary=mock_primary, fallbacks=[mock_fallback])
        request = LLMRequest(system_prompt="sys", user_message="hello")
        response = await router.complete(request)
        assert response.content == "fallback response"
        mock_fallback.complete.assert_called_once()

    async def test_all_providers_fail(self, mock_primary, mock_fallback):
        from harness.llm.base import LLMAllProvidersFailedError
        mock_primary.complete = AsyncMock(side_effect=Exception("Primary down"))
        mock_fallback.complete = AsyncMock(side_effect=Exception("Fallback down"))
        router = ModelRouter(primary=mock_primary, fallbacks=[mock_fallback])
        request = LLMRequest(system_prompt="sys", user_message="hello")
        with pytest.raises(LLMAllProvidersFailedError):
            await router.complete(request)

    def test_active_provider(self, mock_primary):
        router = ModelRouter(primary=mock_primary)
        assert router.active_provider == "test_primary"


# ─── LLMProviderFactory ───────────────────────────────────────

class TestLLMProviderFactory:
    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            LLMProviderFactory.create("nonexistent_provider", api_key="test")

    def test_list_providers_includes_demo(self):
        providers = LLMProviderFactory.list_providers()
        assert "anthropic" in providers
        assert "openai" in providers
        assert "demo" in providers

    def test_create_demo_provider(self):
        provider = LLMProviderFactory.create("demo", api_key="")
        assert provider.provider_name == "demo"
        assert provider.default_model == "demo"

    def test_register_custom_provider(self):
        LLMProviderFactory.register("test_custom", "harness.llm.providers.openai.OpenAIProvider")
        assert "test_custom" in LLMProviderFactory.list_providers()
        # Clean up
        LLMProviderFactory._registry.pop("test_custom", None)
        LLMProviderFactory._cache.pop("test_custom", None)


# ─── OpenAI Provider ──────────────────────────────────────────

class TestOpenAIProvider:
    def _provider(self):
        from harness.llm.providers.openai import OpenAIProvider
        return OpenAIProvider(api_key="test-key")

    async def test_complete_raises_clear_error_on_non_json_response(self):
        provider = self._provider()
        provider.client = AsyncMock()
        provider.client.chat.completions.create = AsyncMock(return_value="<html>404 Not Found</html>")
        from harness.llm.types import LLMRequest
        req = LLMRequest(system_prompt="sys", user_message="hello")
        with pytest.raises(RuntimeError, match="non-JSON"):
            await provider.complete(req)

    async def test_complete_passes_through_normal_response(self):
        provider = self._provider()
        resp = MagicMock()
        choice = MagicMock()
        choice.message.content = "hi"
        choice.message.tool_calls = None
        choice.finish_reason = "stop"
        resp.choices = [choice]
        resp.usage = None
        resp.model = "gpt-4o"
        provider.client = AsyncMock()
        provider.client.chat.completions.create = AsyncMock(return_value=resp)
        from harness.llm.types import LLMRequest
        req = LLMRequest(system_prompt="sys", user_message="hello")
        result = await provider.complete(req)
        assert result.content == "hi"


# ─── LLM Types ────────────────────────────────────────────────

class TestLLMTypes:
    def test_llm_response_defaults(self):
        resp = LLMResponse(content="test")
        assert resp.content == "test"
        assert resp.tool_calls is None
        assert resp.finish_reason == "stop"

    def test_llm_response_with_tool_calls(self):
        resp = LLMResponse(
            content="",
            tool_calls=[{"id": "1", "name": "file_reader", "input": {"path": "/tmp/test"}}],
        )
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0]["name"] == "file_reader"

    def test_llm_request_builds_correctly(self):
        req = LLMRequest(
            system_prompt="You are helpful.",
            user_message="Hello",
            temperature=0.5,
            max_tokens=2048,
        )
        assert req.system_prompt == "You are helpful."
        assert req.user_message == "Hello"
        assert req.messages == []
        assert req.tools is None

    def test_internal_roles_are_normalized_for_provider_messages(self):
        messages = normalize_provider_messages([
            {"role": "observation", "content": "tool output"},
            {"role": "tool", "content": "tool result"},
            {"role": "agent", "content": "agent response"},
            {"role": "unknown", "content": "fallback"},
        ])
        assert [message["role"] for message in messages] == [
            "user",
            "user",
            "assistant",
            "user",
        ]
        assert messages[0]["content"].startswith("Observation:")
        assert messages[1]["content"].startswith("Tool result:")
