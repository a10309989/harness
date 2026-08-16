"""Unit tests for session management and event bus."""

import asyncio
import pytest
from datetime import timedelta

from harness.core.session import SessionManager
from harness.core.events import Event, EventBus
from harness.models.session import SessionState


# ─── SessionManager ────────────────────────────────────────────

class TestSessionManager:
    @pytest.fixture
    def manager(self):
        return SessionManager(session_ttl=timedelta(hours=2), cleanup_interval=300)

    def test_create_session(self, manager):
        session = manager.create_session(user_id="test_user")
        assert session.id != ""
        assert session.user_id == "test_user"
        assert session.state == SessionState.CREATED
        assert session.expires_at is not None
        assert session.last_activity is not None

    def test_get_session(self, manager):
        session = manager.create_session()
        retrieved = manager.get_session(session.id)
        assert retrieved is not None
        assert retrieved.id == session.id

    def test_get_nonexistent_session(self, manager):
        assert manager.get_session("nonexistent") is None

    def test_activate_session(self, manager):
        session = manager.create_session()
        activated = manager.activate_session(session.id)
        assert activated.state == SessionState.ACTIVE

    def test_activate_nonexistent_raises(self, manager):
        with pytest.raises(KeyError, match="Session not found"):
            manager.activate_session("nonexistent")

    def test_end_session(self, manager):
        session = manager.create_session()
        ended = manager.end_session(session.id)
        assert ended.state == SessionState.COMPLETED
        # Should be removed from active sessions
        assert manager.get_session(session.id) is None

    def test_end_nonexistent_raises(self, manager):
        with pytest.raises(KeyError, match="Session not found"):
            manager.end_session("nonexistent")

    def test_add_turn(self, manager):
        session = manager.create_session()
        manager.add_turn(session.id, "user", "Hello")
        manager.add_turn(session.id, "assistant", "Hi there")

        retrieved = manager.get_session(session.id)
        assert len(retrieved.conversation) == 2
        assert retrieved.conversation[0].role == "user"
        assert retrieved.conversation[0].content == "Hello"
        assert retrieved.conversation[1].role == "assistant"

    def test_add_turn_nonexistent_session_no_error(self, manager):
        # Should not raise
        manager.add_turn("nonexistent", "user", "test")

    async def test_cleanup_expired(self, manager):
        session = manager.create_session()
        # Force the session to be expired
        from datetime import datetime, timedelta, timezone
        session.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)

        count = await manager.cleanup_expired()
        assert count >= 1
        assert manager.get_session(session.id) is None

    async def test_close_all(self, manager):
        manager.create_session()
        manager.create_session()
        await manager.close_all()
        assert len(manager._active_sessions) == 0


# ─── EventBus ─────────────────────────────────────────────────

class TestEventBus:
    @pytest.fixture
    def bus(self):
        return EventBus()

    async def test_publish_and_subscribe(self, bus):
        queue = await bus.subscribe("test.event")

        event = Event(event_type="test.event", payload={"key": "value"})
        await bus.publish(event)

        # Should receive the event
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received.event_type == "test.event"
        assert received.payload == {"key": "value"}

    async def test_publish_no_subscribers_no_error(self, bus):
        event = Event(event_type="unsubscribed.event")
        await bus.publish(event)  # Should not raise

    async def test_multiple_subscribers(self, bus):
        q1 = await bus.subscribe("test.event")
        q2 = await bus.subscribe("test.event")

        event = Event(event_type="test.event", payload={})
        await bus.publish(event)

        r1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        r2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        assert r1.event_type == "test.event"
        assert r2.event_type == "test.event"

    async def test_unsubscribe(self, bus):
        queue = await bus.subscribe("test.event")
        bus.unsubscribe("test.event", queue)

        event = Event(event_type="test.event")
        await bus.publish(event)

        # Queue should be empty since we unsubscribed
        assert queue.empty()

    async def test_subscription_count(self, bus):
        await bus.subscribe("event.a")
        await bus.subscribe("event.a")
        await bus.subscribe("event.b")

        counts = bus.subscription_count
        assert counts["event.a"] == 2
        assert counts["event.b"] == 1

    async def test_event_timestamps(self, bus):
        queue = await bus.subscribe("test.event")
        event = Event(event_type="test.event", session_id="s1", source="test")
        await bus.publish(event)

        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received.timestamp is not None
        assert received.session_id == "s1"
        assert received.source == "test"
