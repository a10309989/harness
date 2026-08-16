"""Message models for agent communication."""

from datetime import datetime, timezone
from enum import StrEnum

from harness.models.common import HarnessBaseModel


class MessageType(StrEnum):
    REQUEST = "request"
    RESPONSE = "response"
    EVENT = "event"
    ERROR = "error"
    STREAM_START = "stream_start"
    STREAM_CHUNK = "stream_chunk"
    STREAM_END = "stream_end"


class AgentMessage(HarnessBaseModel):
    """Standard inter-agent message envelope."""

    message_id: str
    correlation_id: str | None = None
    message_type: MessageType
    source_agent: str
    target_agent: str | None = None
    session_id: str
    payload: dict = {}
    metadata: dict = {}
    timestamp: datetime = datetime.now(timezone.utc)
    ttl_seconds: int = 300
