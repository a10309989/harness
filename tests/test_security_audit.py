"""Tests for phase-one identity, trace, audit, and outbox governance."""

import json

import pytest

from harness.core.events import EventBus
from harness.observability.audit import AuditService
from harness.observability.context import (
    ExecutionContext,
    child_span,
    reset_execution_context,
    set_execution_context,
)
from harness.observability.outbox import OutboxWorker
from harness.observability.redaction import content_digest, redact
from harness.security.auth import AuthService
from harness.security.models import ActorType


@pytest.fixture
async def governance(db):
    auth = AuthService(db, mode="dev")
    await auth.initialize()
    audit = AuditService(db)
    worker = OutboxWorker(db, audit, EventBus(), poll_interval=0.01)
    yield db, auth, audit, worker


async def test_development_actor_has_admin_permissions(governance):
    _, auth, _, _ = governance
    actor = await auth.authenticate(None)

    assert actor.actor_id == "default-user"
    assert "admin" in actor.roles
    assert actor.has_permission("audit:read")
    assert actor.has_permission("config:write")


async def test_api_key_is_hashed_and_authenticates(governance):
    db, auth, _, _ = governance
    raw_key, metadata = await auth.create_api_key("default-user")

    row = await db.fetch_one(
        "SELECT key_hash, key_prefix FROM api_credentials WHERE id = $1",
        (metadata["id"],),
    )
    assert raw_key not in row["key_hash"]
    assert row["key_prefix"] == raw_key[:12]

    actor = await auth.authenticate(f"Bearer {raw_key}")
    assert actor.actor_id == "default-user"
    assert actor.auth_method == "api_key"


async def test_actor_roles_and_api_key_revocation(governance):
    _, auth, _, _ = governance
    actor = await auth.create_actor(
        actor_id="ci-service",
        actor_type=ActorType.SERVICE,
        display_name="CI Service",
        roles=("viewer",),
    )
    assert actor.has_permission("config:read")
    assert not actor.has_permission("config:write")

    actor = await auth.set_actor_roles("ci-service", ("operator",))
    assert actor.has_permission("agent:invoke")

    raw_key, metadata = await auth.create_api_key("ci-service")
    assert (await auth.authenticate(f"Bearer {raw_key}")).actor_id == "ci-service"
    assert await auth.revoke_api_key(metadata["id"]) is True
    assert await auth._authenticate_api_key(raw_key) is None


async def test_outbox_persists_trace_and_valid_hash_chain(governance):
    _, auth, audit, worker = governance
    actor = await auth.authenticate(None)
    token = set_execution_context(
        ExecutionContext(trace_id="trace-1", span_id="root-span", actor=actor)
    )
    try:
        await audit.record(
            "test.started",
            resource_type="test",
            resource_id="resource-1",
            input_data={"api_key": "super-secret", "value": 42},
            metadata={"authorization": "Bearer hidden", "safe": "visible"},
        )
        with child_span():
            await audit.record(
                "test.completed",
                resource_type="test",
                resource_id="resource-1",
                decision="success",
                output_data={"result": "ok"},
            )
    finally:
        reset_execution_context(token)

    assert await worker.drain_once() == 2
    assert await worker.drain_once() == 0

    events = await audit.list_events(trace_id="trace-1")
    assert {event["event_type"] for event in events} == {
        "test.started",
        "test.completed",
    }
    started = next(event for event in events if event["event_type"] == "test.started")
    completed = next(event for event in events if event["event_type"] == "test.completed")
    assert started["metadata"]["authorization"] == "[REDACTED]"
    assert completed["parent_span_id"] == "root-span"
    assert (await audit.verify_chain())["valid"] is True


async def test_hash_chain_detects_tampering(governance):
    db, auth, audit, worker = governance
    actor = await auth.authenticate(None)
    token = set_execution_context(
        ExecutionContext(trace_id="trace-tamper", span_id="span", actor=actor)
    )
    try:
        await audit.record("test.event", metadata={"value": "original"})
    finally:
        reset_execution_context(token)
    await worker.drain_once()

    await db.execute(
        "UPDATE audit_events SET metadata = $1 WHERE trace_id = $2",
        (json.dumps({"value": "modified"}), "trace-tamper"),
    )
    await db.commit()

    verification = await audit.verify_chain()
    assert verification["valid"] is False
    assert verification["failed_event_id"] is not None


async def test_business_change_and_outbox_can_rollback_together(governance):
    db, auth, audit, _ = governance
    actor = await auth.authenticate(None)
    token = set_execution_context(
        ExecutionContext(trace_id="trace-transaction", span_id="span", actor=actor)
    )
    try:
        with pytest.raises(RuntimeError):
            async with db.transaction() as connection:
                await connection.execute(
                    """INSERT INTO actors
                       (id, actor_type, tenant_id, display_name, enabled)
                       VALUES ('rollback-actor', 'service', 'default', 'Rollback', 1)"""
                )
                await audit.record(
                    "identity.actor_created",
                    resource_type="actor",
                    resource_id="rollback-actor",
                    connection=connection,
                )
                raise RuntimeError("rollback")
    finally:
        reset_execution_context(token)

    assert await db.fetch_one(
        "SELECT id FROM actors WHERE id = 'rollback-actor'"
    ) is None
    assert await db.fetch_one(
        "SELECT id FROM outbox_events WHERE payload LIKE '%rollback-actor%'"
    ) is None


def test_redaction_and_digest_are_stable():
    value = {
        "api_key": "secret",
        "nested": {"password": "hidden", "safe": "visible"},
    }
    assert redact(value) == {
        "api_key": "[REDACTED]",
        "nested": {"password": "[REDACTED]", "safe": "visible"},
    }
    assert content_digest(value) == content_digest(value)
