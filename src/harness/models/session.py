"""Session models."""

from datetime import datetime, timedelta, timezone
from enum import StrEnum

from harness.models.common import HarnessBaseModel, TimestampedModel


class SessionState(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    ERROR = "error"


class ConversationTurn(HarnessBaseModel):
    """A single turn in a conversation."""

    role: str  # "user", "assistant", "system", "agent"
    content: str
    timestamp: datetime = datetime.now(timezone.utc)
    metadata: dict = {}


class Session(TimestampedModel):
    """A user session with conversation history."""

    id: str
    user_id: str = "default"
    tenant_id: str = "default"
    state: SessionState = SessionState.CREATED
    conversation: list[ConversationTurn] = []
    current_agent: str | None = None
    context_data: dict = {}
    expires_at: datetime | None = None
    last_activity: datetime | None = None
    deleted_by: str | None = None
    deleted_at: datetime | None = None

    def touch(self) -> None:
        """Refresh last activity timestamp and extend expiry."""
        self.last_activity = datetime.now(timezone.utc)
        self.expires_at = self.last_activity + timedelta(hours=2)
