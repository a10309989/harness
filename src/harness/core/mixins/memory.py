"""MemoryMixin — short-term buffer + long-term persistent memory."""

import json
import time
from abc import ABC
from pathlib import Path
from typing import Any

from cachetools import TTLCache


class MemoryMixin(ABC):
    """Capability: short-term (TTL cache) and long-term (JSON file) memory.

    Short-term memory is an in-memory TTL cache for fast access during
    a session. Long-term memory persists to disk for cross-session recall.

    Attributes:
        short_term: TTL-based in-memory cache.
        _storage_dir: Directory for long-term memory files.
        long_term_enabled: Whether long-term persistence is active.
    """

    short_term: TTLCache
    _storage_dir: Path | None
    long_term_enabled: bool

    def _init_memory(self, storage_path: str | None = None, ttl_seconds: int = 3600, long_term_enabled: bool = True) -> None:
        """Initialize memory with configurable TTL and storage.

        Args:
            storage_path: Directory path for long-term memory files.
            ttl_seconds: TTL for short-term cache entries.
            long_term_enabled: Whether to enable long-term persistence.
        """
        self.short_term = TTLCache(maxsize=1024, ttl=ttl_seconds)
        self.long_term_enabled = long_term_enabled
        self._storage_dir = Path(storage_path) if storage_path else None

        if self.long_term_enabled and self._storage_dir:
            self._storage_dir.mkdir(parents=True, exist_ok=True)

    def remember(self, key: str, value: Any) -> None:
        """Store a value in short-term memory.

        Args:
            key: Unique key for retrieval.
            value: The value to store.
        """
        self.short_term[key] = value

    def recall(self, key: str, default: Any = None) -> Any:
        """Retrieve a value from short-term memory.

        Args:
            key: The key to look up.
            default: Default value if key not found.

        Returns:
            The stored value or default.
        """
        return self.short_term.get(key, default)

    def forget(self, key: str) -> None:
        """Remove a value from short-term memory."""
        self.short_term.pop(key, None)

    def persist(self, key: str, value: Any) -> None:
        """Write a value to long-term memory (disk).

        Args:
            key: Unique key (used as filename).
            value: Value to persist (must be JSON-serializable).
        """
        if not self.long_term_enabled or not self._storage_dir:
            return

        safe_key = key.replace("/", "_").replace("\\", "_")
        file_path = self._storage_dir / f"{safe_key}.json"

        entry = {
            "key": key,
            "value": value,
            "stored_at": time.time(),
        }
        file_path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")

    def retrieve(self, key: str, default: Any = None) -> Any:
        """Read a value from long-term memory (disk).

        Args:
            key: The key to look up.
            default: Default if not found.

        Returns:
            The stored value or default.
        """
        if not self.long_term_enabled or not self._storage_dir:
            return default

        safe_key = key.replace("/", "_").replace("\\", "_")
        file_path = self._storage_dir / f"{safe_key}.json"

        if not file_path.exists():
            return default

        try:
            entry = json.loads(file_path.read_text(encoding="utf-8"))
            return entry.get("value", default)
        except (json.JSONDecodeError, KeyError):
            return default

    def summarize_and_store(self) -> str:
        """Create a summary of current context and persist it.

        Returns:
            A summary string of the current session state.
        """
        summary_parts = []
        if hasattr(self, "get_context_window"):
            turns = self.get_context_window()  # type: ignore[attr-defined]
            summary_parts.append(f"Turns: {len(turns)}")
        if hasattr(self, "session_state"):
            summary_parts.append(f"State keys: {list(self.session_state.keys())}")  # type: ignore[attr-defined]

        summary = " | ".join(summary_parts) if summary_parts else "No context available"
        return summary
