"""Database package — PostgreSQL persistence for Harness."""

from harness.db.protocols import DatabaseProtocol
from harness.db.postgres import PostgresDatabase

__all__ = ["PostgresDatabase", "DatabaseProtocol"]


def create_database(database_url: str) -> PostgresDatabase:
    """Build the database adapter from a URL.

    Harness runs exclusively on PostgreSQL.
    """
    if database_url.startswith(("postgresql://", "postgresql+asyncpg://")):
        return PostgresDatabase(database_url.replace("postgresql+asyncpg://", "postgresql://", 1))
    raise ValueError(
        "Unsupported database URL; Harness requires a postgresql:// URL"
    )