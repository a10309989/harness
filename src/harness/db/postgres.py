"""PostgreSQL implementation of the Harness database port."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager

from harness.db.postgres_migrations import apply_postgres_migrations


class PostgresCursor:
    def __init__(self, rows=None, status: str = "") -> None:
        self._rows = rows or []
        self.rowcount = int(status.rsplit(" ", 1)[-1]) if status.rsplit(" ", 1)[-1].isdigit() else 0

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows


class PostgresConnection:
    def __init__(self, connection) -> None:
        self._connection = connection

    async def execute(self, sql: str, params: tuple = ()) -> PostgresCursor:
        self._reject_sqlite_compat(sql)
        if sql.lstrip().upper().startswith(("SELECT", "WITH")):
            return PostgresCursor(await self._connection.fetch(sql, *params))
        return PostgresCursor(status=await self._connection.execute(sql, *params))

    @staticmethod
    def _reject_sqlite_compat(sql: str) -> None:
        normalized = sql.lstrip().upper()
        if "?" in sql or "INSERT OR IGNORE" in normalized or "ROWID" in normalized:
            raise ValueError("PostgreSQL repositories must use native PostgreSQL SQL")


class PostgresDatabase:
    """AsyncPG-backed database with the same repository-facing contract as SQLite."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._pool = None

    async def initialize(self) -> None:
        import asyncpg

        self._pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=10)
        async with self._pool.acquire() as connection:
            await apply_postgres_migrations(
                connection,
                Path(__file__).resolve().parents[3] / "postgres" / "migrations",
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def execute(self, sql: str, params: tuple = ()) -> PostgresCursor:
        async with self._pool.acquire() as connection:
            return await PostgresConnection(connection).execute(sql, params)

    async def fetch_one(self, sql: str, params: tuple = ()):
        async with self._pool.acquire() as connection:
            PostgresConnection._reject_sqlite_compat(sql)
            return self._normalize_row(await connection.fetchrow(sql, *params))

    async def fetch_all(self, sql: str, params: tuple = ()):
        async with self._pool.acquire() as connection:
            PostgresConnection._reject_sqlite_compat(sql)
            rows = await connection.fetch(sql, *params)
            return [self._normalize_row(row) for row in rows]

    @classmethod
    def _normalize_row(cls, record):
        """Convert a Postgres row to a plain dict, serializing datetimes as
        UTC-aware ISO strings. This matches the ISO-string behavior the SQLite
        adapter exposed AND the ``+00:00`` suffix the audit hash chain hashes
        against, so services that json.dumps rows or recompute hashes work."""
        if record is None:
            return None
        normalized = {}
        for key, value in dict(record).items():
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                else:
                    value = value.astimezone(timezone.utc)
                normalized[key] = value.isoformat()
            else:
                normalized[key] = value
        return normalized

    async def commit(self) -> None:
        return None

    @asynccontextmanager
    async def transaction(self):
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                yield PostgresConnection(connection)

    @staticmethod
    def to_json(obj):
        return json.dumps(obj) if obj is not None else None

    @staticmethod
    def from_json(value):
        return json.loads(value) if value else None
