import asyncio

import pytest

from harness.db.postgres_migrations import apply_postgres_migrations


class FakeConnection:
    def __init__(self):
        self.rows = {}

    async def execute(self, sql, *args):
        if sql.startswith("INSERT INTO public.schema_migrations"):
            self.rows[args[0]] = {"checksum": args[2]}

    async def fetchrow(self, sql, version):
        return self.rows.get(version)

    async def fetchval(self, sql):
        return None

    def transaction(self):
        class Transaction:
            async def __aenter__(self): pass
            async def __aexit__(self, *args): pass
        return Transaction()


async def test_postgres_migrations_record_and_verify_checksums(tmp_path):
    migration = tmp_path / "0001_baseline.sql"
    migration.write_text("CREATE TABLE example (id INTEGER);", encoding="utf-8")
    connection = FakeConnection()

    await apply_postgres_migrations(connection, tmp_path)
    await apply_postgres_migrations(connection, tmp_path)

    migration.write_text("CREATE TABLE changed (id INTEGER);", encoding="utf-8")
    with pytest.raises(RuntimeError, match="checksum"):
        await apply_postgres_migrations(connection, tmp_path)
