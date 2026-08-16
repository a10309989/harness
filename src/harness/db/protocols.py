"""Database interfaces shared by storage adapters and repositories."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DatabaseConnection(Protocol):
    """Minimal connection contract used inside repository transactions."""

    async def execute(self, sql: str, params: tuple = ()) -> Any:
        """Execute a parameterized statement."""


@runtime_checkable
class DatabaseProtocol(Protocol):
    """Storage port implemented by SQLite now and PostgreSQL later."""

    async def initialize(self) -> None:
        """Initialize the backing store."""

    async def close(self) -> None:
        """Close backing resources."""

    async def execute(self, sql: str, params: tuple = ()) -> Any:
        """Execute a parameterized statement."""

    async def fetch_one(self, sql: str, params: tuple = ()) -> Any:
        """Fetch one row."""

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[Any]:
        """Fetch all rows."""

    async def commit(self) -> None:
        """Commit pending changes."""

    def transaction(self) -> AbstractAsyncContextManager[DatabaseConnection]:
        """Open an atomic transaction."""

    @staticmethod
    def to_json(obj: Any) -> str | None:
        """Serialize a JSON-compatible value."""

    @staticmethod
    def from_json(value: str | None) -> Any:
        """Deserialize a JSON-compatible value."""
