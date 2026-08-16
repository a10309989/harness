"""Anthropic Claude provider implementation.

base_url handling:
- The Anthropic SDK (AsyncAnthropic) appends "/messages" to base_url.
- So if the user provides:  https://foo.com/apps/anthropic/v1/messages
  we normalize to:          https://foo.com/apps/anthropic/v1
  and the SDK calls:        https://foo.com/apps/anthropic/v1/messages

- If user provides bare https://foo.com/apps/anthropic/v1, we keep it as-is.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
from anthropic import AsyncAnthropic

from harness.llm.base import AbstractLLMProvider
from harness.llm.types import LLMRequest, LLMResponse, LLMStreamChunk, TokenUsage, normalize_provider_messages


def _normalize_base_url(raw_url: str) -> str:
    """Normalize a user-provided base_url for the Anthropic SDK.

    The SDK appends "/messages" to base_url.
    Strategy: keep base_url exactly as the user provided it (minus trailing slash).
    If the user provides the full endpoint path (.../v1/messages), the SDK will
    append another "/messages", getting .../v1/messages/messages (bad).
    So we only strip a trailing "/messages" to prevent double-append.
    """
    url = raw_url.rstrip("/")
    if url.endswith("/messages"):
        url = url[: -len("/messages")]
    return url


class AnthropicProvider(AbstractLLMProvider):
    """Anthropic Claude LLM provider.

    base_url: user provides the FULL endpoint (including /messages).
    SDK appends /messages → we strip /messages from user input to prevent double appending.
    Result: .../anthropic/v1 + SDK /messages = .../anthropic/v1/messages ✓
    """

    provider_name = "anthropic"

    def __init__(self, api_key: str, default_model: str = "claude-sonnet-4-20250514", base_url: str | None = None) -> None:
        super().__init__(api_key, default_model)
        client_kwargs = {"api_key": api_key}
        self._raw_base_url = base_url
        if base_url:
            normalized = _normalize_base_url(base_url)
            client_kwargs["base_url"] = normalized
        # 使用自定义 http_client 完全绕过系统代理（trust_env=False + proxy=None），
        # 避免本地代理干扰国内大模型连接；显式设置较长超时（默认 5s 太短）。
        http_client = httpx.AsyncClient(trust_env=False, proxy=None, timeout=300.0)
        client_kwargs["http_client"] = http_client
        client_kwargs["timeout"] = 300.0
        self.client = AsyncAnthropic(**client_kwargs)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        messages = self._build_messages(request)
        model = request.metadata.get("model", self.default_model)

        kwargs: dict[str, Any] = {
            "model": model,
            "system": request.system_prompt,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }

        if request.tools:
            kwargs["tools"] = self._convert_tools(request.tools)

        if request.stop_sequences:
            kwargs["stop_sequences"] = request.stop_sequences

        response = await self.client.messages.create(**kwargs)

        content = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        usage = None
        if response.usage:
            usage = TokenUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0),
                cache_write_tokens=getattr(response.usage, "cache_creation_input_tokens", 0),
            )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls if tool_calls else None,
            finish_reason=response.stop_reason or "stop",
            usage=usage,
            model=response.model,
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        messages = self._build_messages(request)
        model = request.metadata.get("model", self.default_model)

        kwargs: dict[str, Any] = {
            "model": model,
            "system": request.system_prompt,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }

        if request.tools:
            kwargs["tools"] = self._convert_tools(request.tools)

        async with self.client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        yield LLMStreamChunk(content_delta=event.delta.text)
                elif event.type == "message_stop":
                    yield LLMStreamChunk(finish_reason="stop")

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        raise NotImplementedError(
            "Anthropic does not provide a dedicated embedding API. "
            "Use OpenAI embeddings or Voyage AI instead."
        )

    def count_tokens(self, text: str, model: str | None = None) -> int:
        # Approximate: Claude uses ~1 token per 3.5 chars for English
        return len(text) // 3

    def _build_messages(self, request: LLMRequest) -> list[dict]:
        messages = [
            message for message in normalize_provider_messages(request.messages)
            if message["role"] != "system"
        ]
        messages.append({"role": "user", "content": request.user_message})
        return messages

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convert OpenAI-style tool definitions to Anthropic format."""
        converted = []
        for tool in tools:
            converted.append({
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": tool.get("input_schema", tool.get("parameters", {})),
            })
        return converted
