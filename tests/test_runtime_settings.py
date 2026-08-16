import pytest

from harness.runtime.settings import RuntimeSettings
from harness.security.permissions import ROLE_PERMISSIONS


def test_runtime_settings_reads_environment(monkeypatch):
    monkeypatch.setenv("HARNESS_DATABASE_URL", "postgresql://harness@localhost/harness")
    monkeypatch.setenv("HARNESS_CORS_ALLOWED_ORIGINS", "https://one.example, https://two.example")

    settings = RuntimeSettings()

    assert settings.database_url == "postgresql://harness@localhost/harness"
    assert settings.allowed_origins == ["https://one.example", "https://two.example"]


def test_create_database_accepts_postgres_rejects_sqlite():
    from harness.db import create_database
    from harness.db.postgres import PostgresDatabase

    assert isinstance(
        create_database("postgresql://harness@localhost/harness"), PostgresDatabase
    )
    with pytest.raises(ValueError, match="postgresql"):
        create_database("sqlite+aiosqlite:///tmp/harness.db")


def test_temporal_authoritative_execution_requires_temporal():
    with pytest.raises(ValueError, match="TEMPORAL_AUTHORITATIVE_EXECUTION"):
        RuntimeSettings(temporal_authoritative_execution=True)


def test_production_refuses_dev_security_mode():
    with pytest.raises(ValueError, match="HARNESS_SECURITY_MODE"):
        RuntimeSettings(environment="production", security_mode="dev")


def test_production_allows_explicit_non_dev_security_mode():
    settings = RuntimeSettings(environment="production", security_mode="api-key")

    assert settings.security_mode == "api-key"


def test_operator_permissions_cover_new_operational_routes():
    permissions = ROLE_PERMISSIONS["operator"]

    assert {"execution:invoke", "knowledge:write", "test_case:write", "skill:read"} <= permissions
