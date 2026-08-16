"""Unified types for the LLM abstraction layer."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass
class TokenUsage:
    """Token usage statistics."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class LLMResponse:
    """Unified response from any LLM provider."""

    content: str
    tool_calls: list[dict] | None = None
    finish_reason: str = "stop"
    usage: TokenUsage | None = None
    model: str = ""


@dataclass
class LLMStreamChunk:
    """A single chunk from a streaming LLM response."""

    content_delta: str = ""
    tool_call_delta: dict | None = None
    finish_reason: str | None = None


class LLMRequest(BaseModel):
    """Unified request for any LLM provider."""

    system_prompt: str
    user_message: str
    messages: list[dict] = []  # conversation history
    tools: list[dict] | None = None  # JSON Schema tool definitions
    temperature: float = 0.2
    max_tokens: int = 4096
    stop_sequences: list[str] | None = None
    metadata: dict = {}


def normalize_provider_messages(messages: list[dict]) -> list[dict]:
    """Normalize internal conversation roles before sending them to model providers.

    Agents may keep internal turns such as ``observation`` in their working
    memory. External chat/message APIs generally only accept user, assistant,
    and sometimes system roles. This function preserves the information while
    mapping internal roles to provider-safe roles.
    """
    normalized: list[dict] = []
    allowed_roles = {"user", "assistant", "system"}
    role_aliases = {
        "human": "user",
        "ai": "assistant",
        "agent": "assistant",
        "tool": "user",
        "function": "user",
        "observation": "user",
    }
    prefixes = {
        "tool": "Tool result",
        "function": "Function result",
        "observation": "Observation",
    }
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        raw_role = str(message.get("role") or "user").lower()
        role = role_aliases.get(raw_role, raw_role)
        if role not in allowed_roles:
            role = "user"
        content = message.get("content", "")
        if content is None:
            content = ""
        if raw_role in prefixes:
            content = f"{prefixes[raw_role]}:\n{content}"
        normalized.append({"role": role, "content": str(content)})
    return normalized
