"""Redis Streams publisher for durable Outbox events."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from harness.core.events import Event
from harness.observability.otel import traced_span

EventHandler = Callable[[Event], Awaitable[None]]


class RedisEventPublisher:
    """Publishes an outbox event once, then lets consumer groups fan it out."""

    def __init__(self, client, *, stream: str, dedupe_ttl_seconds: int) -> None:
        self._client = client
        self._stream = stream
        self._dedupe_ttl_seconds = dedupe_ttl_seconds

    @classmethod
    async def connect(cls, settings) -> "RedisEventPublisher":
        from redis.asyncio import Redis

        client = Redis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        return cls(
            client,
            stream=settings.redis_event_stream,
            dedupe_ttl_seconds=settings.redis_event_ttl_seconds,
        )

    async def publish(self, outbox_event_id: str, event: Event) -> None:
        dedupe_key = f"harness:outbox:{outbox_event_id}"
        created = await self._client.set(
            dedupe_key,
            "1",
            nx=True,
            ex=self._dedupe_ttl_seconds,
        )
        if not created:
            return
        try:
            await self._client.xadd(
                self._stream,
                {
                    "event_id": outbox_event_id,
                    "event_type": event.event_type,
                    "session_id": event.session_id,
                    "source": event.source,
                    "timestamp": event.timestamp.isoformat(),
                    "payload": json.dumps(event.payload, sort_keys=True),
                },
                maxlen=10_000,
                approximate=True,
            )
        except Exception:
            await self._client.delete(dedupe_key)
            raise

    async def close(self) -> None:
        await self._client.aclose()


@dataclass
class RedisConsumerStats:
    processed: int = 0
    retried: int = 0
    dlq: int = 0
    reclaimed: int = 0


class RedisStreamConsumer:
    """Consumer group worker for realtime projections with retry and DLQ."""

    def __init__(
        self,
        client,
        *,
        stream: str,
        group: str,
        consumer: str,
        handler: EventHandler,
        dlq_stream: str = "harness:events:dlq",
        retry_zset: str = "harness:events:retry",
        max_retries: int = 5,
        pending_idle_ms: int = 30_000,
        count: int = 50,
    ) -> None:
        self._client = client
        self._stream = stream
        self._group = group
        self._consumer = consumer
        self._handler = handler
        self._dlq_stream = dlq_stream
        self._retry_zset = retry_zset
        self._max_retries = max_retries
        self._pending_idle_ms = pending_idle_ms
        self._count = count
        self._group_ready = False

    async def drain_once(self) -> RedisConsumerStats:
        with traced_span("redis.consumer.drain", stream=self._stream, group=self._group):
            await self._ensure_group()
            stats = RedisConsumerStats()
            await self._promote_due_retries()
            for message_id, fields in await self._claim_pending():
                stats.reclaimed += 1
                await self._handle(message_id, fields, stats)
            for message_id, fields in await self._read_new():
                await self._handle(message_id, fields, stats)
            return stats

    async def metrics_snapshot(self) -> dict[str, int]:
        pending = await self._client.xpending(self._stream, self._group)
        last_entries = await self._client.xrevrange(self._stream, count=1)
        dlq_length = await self._client.xlen(self._dlq_stream)
        retry_length = await self._client.zcard(self._retry_zset)
        last_message_ms = 0
        if last_entries:
            last_message_ms = int(str(last_entries[0][0]).split("-", 1)[0])
        return {
            "stream_backlog": await self._client.xlen(self._stream),
            "pending": int(pending.get("pending", 0) if isinstance(pending, dict) else 0),
            "retry_backlog": int(retry_length),
            "dlq_backlog": int(dlq_length),
            "consumer_lag_ms": max(0, self._now_ms() - last_message_ms) if last_message_ms else 0,
        }

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self._client.xgroup_create(
                self._stream,
                self._group,
                id="0",
                mkstream=True,
            )
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    async def _read_new(self) -> list[tuple[str, dict]]:
        response = await self._client.xreadgroup(
            self._group,
            self._consumer,
            streams={self._stream: ">"},
            count=self._count,
            block=0,
        )
        return self._flatten(response)

    async def _claim_pending(self) -> list[tuple[str, dict]]:
        pending = await self._client.xpending_range(
            self._stream,
            self._group,
            min="-",
            max="+",
            count=self._count,
        )
        ids = [
            item["message_id"]
            for item in pending
            if item.get("time_since_delivered", 0) >= self._pending_idle_ms
        ]
        if not ids:
            return []
        claimed = await self._client.xclaim(
            self._stream,
            self._group,
            self._consumer,
            min_idle_time=self._pending_idle_ms,
            message_ids=ids,
        )
        return [(message_id, fields) for message_id, fields in claimed]

    async def _handle(
        self,
        message_id: str,
        fields: dict,
        stats: RedisConsumerStats,
    ) -> None:
        with traced_span(
            "redis.consumer.handle",
            stream=self._stream,
            event_type=fields.get("event_type"),
            message_id=message_id,
        ):
            if self._not_before(fields) > self._now_ms():
                await self._schedule_retry(message_id, fields, error="retry not due")
                await self._client.xack(self._stream, self._group, message_id)
                stats.retried += 1
                return
            try:
                await self._handler(self._event_from_fields(fields))
                await self._client.xack(self._stream, self._group, message_id)
                stats.processed += 1
            except Exception as exc:
                attempts = int(fields.get("attempts", 0)) + 1
                if attempts > self._max_retries:
                    await self._send_to_dlq(message_id, fields, attempts, str(exc))
                    stats.dlq += 1
                else:
                    retry_fields = dict(fields)
                    retry_fields["attempts"] = str(attempts)
                    retry_fields["not_before_ms"] = str(
                        self._now_ms() + min(60_000, 1000 * (2 ** attempts))
                    )
                    await self._schedule_retry(message_id, retry_fields, error=str(exc))
                    stats.retried += 1
                await self._client.xack(self._stream, self._group, message_id)

    async def _schedule_retry(self, message_id: str, fields: dict, *, error: str) -> None:
        payload = dict(fields)
        payload["original_message_id"] = message_id
        payload["last_error"] = error[:1000]
        await self._client.zadd(
            self._retry_zset,
            {json.dumps(payload, sort_keys=True): int(payload["not_before_ms"])},
        )

    async def _promote_due_retries(self) -> None:
        now_ms = self._now_ms()
        members = await self._client.zrangebyscore(
            self._retry_zset,
            0,
            now_ms,
            start=0,
            num=self._count,
        )
        for member in members:
            fields = json.loads(member)
            await self._client.zrem(self._retry_zset, member)
            await self._client.xadd(self._stream, fields, maxlen=10_000, approximate=True)

    async def _send_to_dlq(
        self,
        message_id: str,
        fields: dict,
        attempts: int,
        error: str,
    ) -> None:
        dlq_fields = dict(fields)
        dlq_fields.update(
            {
                "attempts": str(attempts),
                "original_message_id": message_id,
                "failed_at_ms": str(self._now_ms()),
                "last_error": error[:1000],
            }
        )
        await self._client.xadd(
            self._dlq_stream,
            dlq_fields,
            maxlen=10_000,
            approximate=True,
        )

    @staticmethod
    def _flatten(response) -> list[tuple[str, dict]]:
        flattened = []
        for _, messages in response or []:
            flattened.extend(messages)
        return flattened

    @staticmethod
    def _event_from_fields(fields: dict) -> Event:
        timestamp = fields.get("timestamp")
        return Event(
            event_type=fields["event_type"],
            payload=json.loads(fields.get("payload") or "{}"),
            session_id=fields.get("session_id", ""),
            source=fields.get("source", ""),
            timestamp=datetime.now(timezone.utc) if not timestamp else datetime.fromisoformat(timestamp),
        )

    @staticmethod
    def _not_before(fields: dict) -> int:
        return int(fields.get("not_before_ms") or 0)

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)
