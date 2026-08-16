"""Background delivery of durable outbox events."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from harness.core.events import Event, EventBus
from harness.db.protocols import DatabaseProtocol
from harness.observability.audit import AuditService

logger = logging.getLogger(__name__)


class OutboxWorker:
    def __init__(
        self,
        db: DatabaseProtocol,
        audit_service: AuditService,
        event_bus: EventBus,
        redis_publisher=None,
        *,
        poll_interval: float = 0.1,
        lease_seconds: int = 30,
    ) -> None:
        self.db = db
        self.audit_service = audit_service
        self.event_bus = event_bus
        self.redis_publisher = redis_publisher
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self.owner = f"outbox-worker-{uuid4().hex}"
        self._stopping = asyncio.Event()

    async def run(self) -> None:
        while not self._stopping.is_set():
            processed = await self.drain_once(limit=50)
            if processed == 0:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_interval)
                except asyncio.TimeoutError:
                    pass

    async def stop(self) -> None:
        self._stopping.set()
        await self.drain_once(limit=500)

    async def drain_once(self, limit: int = 50) -> int:
        now = datetime.now(timezone.utc).isoformat()
        rows = await self._claim(now, limit)
        processed = 0
        for row in rows:
            try:
                payload = json.loads(row["payload"])
                event = Event(
                    event_type=payload.get("event_type", row["topic"]),
                    payload=payload,
                    session_id=payload.get("session_id") or "",
                    source=payload.get("actor_id", "system"),
                )
                if row["topic"] == "audit":
                    inserted = await self.audit_service.persist_audit_payload(payload)
                    if inserted:
                        await self.event_bus.publish(event)
                else:
                    await self.event_bus.publish(event)
                if self.redis_publisher is not None:
                    await self.redis_publisher.publish(row["id"], event)
                await self._mark_processed(row["id"])
                await self.db.commit()
                processed += 1
            except Exception as exc:
                attempts = row["attempts"] + 1
                delay = min(60, 2 ** min(attempts, 6))
                available_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=delay)
                ).isoformat()
                status = "failed" if attempts >= 10 else "pending"
                await self._mark_failed(
                    row["id"],
                    status=status,
                    attempts=attempts,
                    available_at=available_at,
                    error=str(exc)[:1000],
                )
                await self.db.commit()
                logger.exception("Outbox event delivery failed: %s", row["id"])
        return processed

    async def _mark_processed(self, event_id: str) -> None:
        processed_at = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            """UPDATE outbox_events
               SET status = 'processed', processed_at = $1, attempts = attempts + 1,
                   lease_owner = NULL, lease_expires_at = NULL
               WHERE id = $2 AND lease_owner = $3""",
            (
                self._postgres_timestamp(processed_at),
                event_id,
                self.owner,
            ),
        )

    async def _mark_failed(
        self,
        event_id: str,
        *,
        status: str,
        attempts: int,
        available_at: str,
        error: str,
    ) -> None:
        await self.db.execute(
            """UPDATE outbox_events
               SET status = $1, attempts = $2, available_at = $3, last_error = $4,
                   lease_owner = NULL, lease_expires_at = NULL
               WHERE id = $5 AND lease_owner = $6""",
            (
                status,
                attempts,
                self._postgres_timestamp(available_at),
                error,
                event_id,
                self.owner,
            ),
        )

    async def _claim(self, now: str, limit: int):
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds)
        ).isoformat()
        postgres_now = self._postgres_timestamp(now)
        postgres_expires_at = self._postgres_timestamp(expires_at)
        async with self.db.transaction() as connection:
            rows = await (
                await connection.execute(
                    """WITH candidates AS (
                           SELECT id FROM outbox_events
                           WHERE available_at <= $1
                             AND (status = 'pending' OR (status = 'processing' AND lease_expires_at < $1))
                           ORDER BY created_at
                           FOR UPDATE SKIP LOCKED
                           LIMIT $2
                       )
                       UPDATE outbox_events AS event
                       SET status = 'processing', lease_owner = $3, lease_expires_at = $4
                       FROM candidates
                       WHERE event.id = candidates.id
                       RETURNING event.*""",
                    (postgres_now, limit, self.owner, postgres_expires_at),
                )
            ).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def _postgres_timestamp(value: str | datetime) -> datetime:
        if isinstance(value, datetime):
            source = value
        else:
            source = datetime.fromisoformat(value)
        if source.tzinfo is None:
            return source
        return source.astimezone(timezone.utc).replace(tzinfo=None)
