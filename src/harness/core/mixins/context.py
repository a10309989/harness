"""ContextMixin — conversation window and session state management.

Can optionally delegate conversation storage to an external SessionManager
for unified session management across agents.
"""

from __future__ import annotations

from abc import ABC
from collections import deque
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from harness.models.session import ConversationTurn

if TYPE_CHECKING:
    from harness.core.session import SessionManager


class ContextMixin(ABC):
    """Capability: conversation history window and session state.

    Maintains a sliding window of conversation turns and a key-value
    session state store for sharing data across turns.

    When session_manager is provided, conversation turns are forwarded
    to the centralized SessionManager, making it the single source of truth.
    """

    conversation: deque[ConversationTurn]
    session_state: dict[str, Any]
    max_context_turns: int
    _session_manager: SessionManager | None
    _current_session_id: str | None

    def _init_context(self, max_turns: int = 20) -> None:
        """Initialize the context window.

        Args:
            max_turns: Maximum number of conversation turns to retain.
        """
        self.conversation = deque(maxlen=max_turns)
        self.max_context_turns = max_turns
        self.session_state = {}
        self._session_manager = None
        self._current_session_id = None

    def bind_session_manager(self, session_manager: SessionManager, session_id: str) -> None:
        """Bind an external SessionManager for centralized conversation storage.

        After binding, add_turn() will forward to both the local window
        AND the centralized session manager.

        Args:
            session_manager: The central SessionManager instance.
            session_id: The current session ID to associate turns with.
        """
        self._session_manager = session_manager
        self._current_session_id = session_id

    def unbind_session_manager(self) -> None:
        """Unbind the external session manager."""
        self._session_manager = None
        self._current_session_id = None

    def add_turn(self, role: str, content: str, metadata: dict | None = None) -> ConversationTurn:
        """Add a turn to the conversation history, forwarding to SessionManager if bound.

        Args:
            role: "user", "assistant", "system", or "agent"
            content: The message content.
            metadata: Optional metadata for the turn.

        Returns:
            The created ConversationTurn.
        """
        turn = ConversationTurn(
            role=role,
            content=content,
            timestamp=datetime.now(timezone.utc),
            metadata=metadata or {},
        )
        self.conversation.append(turn)

        # Forward to centralized session manager if bound
        if self._session_manager is not None and self._current_session_id is not None:
            try:
                self._session_manager.add_turn(self._current_session_id, role, content)
            except Exception:
                pass  # Don't let session manager errors break agent flow

        return turn

    def get_context_window(self) -> list[ConversationTurn]:
        """Get the current conversation history.

        Returns:
            List of conversation turns in chronological order.
        """
        return list(self.conversation)

    def get_context_for_llm(self) -> list[dict]:
        """Get context formatted for LLM API calls.

        Returns:
            List of {"role": str, "content": str} dicts.
        """
        return [{"role": t.role, "content": t.content} for t in self.conversation]

    def set_state(self, key: str, value: Any) -> None:
        """Set a session state value.

        Args:
            key: State key.
            value: State value (must be JSON-serializable).
        """
        self.session_state[key] = value

    def get_state(self, key: str, default: Any = None) -> Any:
        """Get a session state value.

        Args:
            key: State key.
            default: Default value if key not found.

        Returns:
            The stored value or default.
        """
        return self.session_state.get(key, default)

    def clear_context(self) -> None:
        """Clear all conversation history and session state."""
        self.conversation.clear()
        self.session_state.clear()

    @property
    def turn_count(self) -> int:
        """Number of turns in the current context window."""
        return len(self.conversation)
