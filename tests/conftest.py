"""Shared test fixtures — PostgreSQL-backed database for service tests.

Harness now runs exclusively on PostgreSQL. The old SQLite-based unit tests that
constructed ``Database(tmp_path / "x.db")`` have been migrated to use the
``db`` fixture below, which provides a fresh Postgres database per test.
"""

from __future__ import annotations

import pytest

from harness.db.postgres import PostgresDatabase

TEST_PG_URL = "postgresql://harness:harness-local-dev@127.0.0.1:5432/harness_test"

# Table truncation order does not matter because we TRUNCATE ... CASCADE.
_TABLES_SQL = (
    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
    "AND tablename <> 'schema_migrations'"
)


@pytest.fixture
async def db():
    """A clean Postgres database, one connection per test event loop."""
    database = PostgresDatabase(TEST_PG_URL)
    await database.initialize()
    rows = await database.fetch_all(_TABLES_SQL)
    tables = ", ".join(f"public.{r['tablename']}" for r in rows)
    await database.execute(f"TRUNCATE {tables} CASCADE")
    try:
        yield database
    finally:
        await database.close()