"""Versioned native PostgreSQL migration runner."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

MIGRATION_NAME = re.compile(r"^(\d{4})_(.+)\.sql$")


async def apply_postgres_migrations(connection, directory: str | Path) -> None:
    """Apply immutable SQL migrations and record their content checksum."""
    lock_key = 7_184_295_031
    await connection.execute("SELECT pg_advisory_lock($1)", lock_key)
    try:
        await connection.execute(
            """CREATE TABLE IF NOT EXISTS public.schema_migrations (
               version INTEGER PRIMARY KEY,
               name TEXT NOT NULL,
               checksum TEXT NOT NULL,
               applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
        )
        root = Path(directory)
        migrations = []
        for path in root.glob("*.sql"):
            match = MIGRATION_NAME.match(path.name)
            if match:
                migrations.append((int(match.group(1)), match.group(2), path))
        for version, name, path in sorted(migrations):
            # utf-8-sig strips a leading UTF-8 BOM, which asyncpg would reject.
            sql = path.read_text(encoding="utf-8-sig")
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            applied = await connection.fetchrow(
                "SELECT checksum FROM public.schema_migrations WHERE version = $1",
                version,
            )
            if applied:
                if applied["checksum"] != checksum:
                    raise RuntimeError(f"Migration {path.name} checksum changed after application")
                continue
            if version == 1:
                existing = await connection.fetchval("SELECT to_regclass('public.sessions')")
                if existing:
                    await connection.execute(
                        "INSERT INTO public.schema_migrations (version, name, checksum) VALUES ($1, $2, $3)",
                        version, name, checksum,
                    )
                    continue
            async with connection.transaction():
                await connection.execute(sql)
                await connection.execute(
                    "INSERT INTO public.schema_migrations (version, name, checksum) VALUES ($1, $2, $3)",
                    version,
                    name,
                    checksum,
                )
    finally:
        await connection.execute("SELECT pg_advisory_unlock($1)", lock_key)
