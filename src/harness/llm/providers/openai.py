"""OpenAI provider implementation."""

from collections.abc import AsyncIterator

import httpx
from openai import AsyncOpenAI

from harness.llm.base import AbstractLLMProvider
from harness.llm.types import LLMRequest, LLMResponse, LLMStreamChunk, TokenUsage, normalize_provider_messages


class OpenAIProvider(AbstractLLMProvider):
    """OpenAI GPT LLM provider."""

    provider_name = "openai"

    def __init__(self, api_key: str, default_model: str = "gpt-4o", base_url: str | None = None) -> None:
        super().__init__(api_key, default_model)
        client_kwargs = {"api_key": api_key}
        self._raw_base_url = base_url
        if base_url:
            client_kwargs["base_url"] = base_url
        # 使用自定义 http_client 绕过系统代理，避免本地代理干扰国内大模型连接
        http_client = httpx.AsyncClient(trust_env=False)
        client_kwargs["http_client"] = http_client
        self.client = AsyncOpenAI(**client_kwargs)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        messages = self._build_messages(request)
        model = request.metadata.get("model", self.default_model)

        kwargs = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }

        if request.tools:
            kwargs["tools"] = [
                {"type": "function", "function": t} for t in request.tools
            ]

        if request.stop_sequences:
            kwargs["stop"] = request.stop_sequences

        response = await self.client.chat.completions.create(**kwargs)
        if isinstance(response, str):
            raise RuntimeError(
                "LLM endpoint returned a non-JSON (plain text) response. "
                "This usually means base_url is wrong, the URL points to a web page, "
                "or the server returned an error page. "
                f"Raw response: {(response[:500]) if response else '<empty>'}"
            )
        choice = response.choices[0]

        content = choice.message.content or ""
        tool_calls = None
        if choice.message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": tc.function.arguments,  # JSON string
                }
                for tc in choice.message.tool_calls
            ]

        usage = None
        if response.usage:
            usage = TokenUsage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
            )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            model=response.model,
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        messages = self._build_messages(request)
        model = request.metadata.get("model", self.default_model)

        kwargs = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": True,
        }

        if request.tools:
            kwargs["tools"] = [
                {"type": "function", "function": t} for t in request.tools
            ]

        stream = await self.client.chat.completions.create(**kwargs)
        if isinstance(stream, str):
            raise RuntimeError(
                "LLM endpoint returned a non-JSON (plain text) response. "
                "This usually means base_url is wrong, the URL points to a web page, "
                "or the server returned an error page. "
                f"Raw response: {(stream[:500]) if stream else '<empty>'}"
            )
        async for chunk in stream:
            if isinstance(chunk, str):
                raise RuntimeError(
                    "LLM streaming endpoint returned a non-JSON (plain text) chunk. "
                    f"Raw chunk: {(chunk[:500]) if chunk else '<empty>'}"
                )
            if chunk.choices and chunk.choices[0].delta:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield LLMStreamChunk(content_delta=delta.content)
            if chunk.choices and chunk.choices[0].finish_reason:
                yield LLMStreamChunk(finish_reason=chunk.choices[0].finish_reason)

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        model = model or "text-embedding-3-small"
        response = await self.client.embeddings.create(model=model, input=texts)
        return [e.embedding for e in response.data]

    def count_tokens(self, text: str, model: str | None = None) -> int:
        # Rough estimate of token count
        return len(text) // 4

    def _build_messages(self, request: LLMRequest) -> list[dict]:
        messages = [{"role": "system", "content": request.system_prompt}]
        messages.extend(normalize_provider_messages(request.messages))
        messages.append({"role": "user", "content": request.user_message})
        return messages
