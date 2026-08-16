import pytest

from harness.core.events import Event
from harness.messaging.redis_dlq import replay_dlq
from harness.messaging.redis_events import RedisEventPublisher, RedisStreamConsumer


class FakeRedis:
    def __init__(self) -> None:
        self.keys = set()
        self.messages = []
        self.groups = set()
        self.new_messages = []
        self.pending = []
        self.claimed = []
        self.acked = []
        self.zset = {}
        self.deleted = []

    async def set(self, key, value, *, nx, ex):
        if key in self.keys:
            return False
        self.keys.add(key)
        return True

    async def xadd(self, stream, fields, *, maxlen, approximate):
        message_id = f"{len(self.messages) + 1}-0"
        self.messages.append((stream, fields))
        return message_id

    async def delete(self, key):
        self.keys.remove(key)

    async def xgroup_create(self, stream, group, *, id, mkstream):
        key = (stream, group)
        if key in self.groups:
            raise RuntimeError("BUSYGROUP Consumer Group name already exists")
        self.groups.add(key)

    async def xreadgroup(self, group, consumer, *, streams, count, block):
        messages = self.new_messages[:count]
        self.new_messages = self.new_messages[count:]
        return [("harness:events", messages)] if messages else []

    async def xpending_range(self, stream, group, *, min, max, count):
        return self.pending[:count]

    async def xclaim(self, stream, group, consumer, *, min_idle_time, message_ids):
        return [
            (message_id, fields)
            for message_id, fields in self.claimed
            if message_id in message_ids
        ]

    async def xack(self, stream, group, message_id):
        self.acked.append((stream, group, message_id))

    async def zadd(self, key, mapping):
        self.zset.update(mapping)

    async def zrangebyscore(self, key, min, max, *, start, num):
        return [
            member
            for member, score in list(self.zset.items())[start:start + num]
            if min <= score <= max
        ]

    async def zrem(self, key, member):
        self.zset.pop(member, None)

    async def xrange(self, stream, *, min, max, count):
        return [
            (f"{index}-0", fields)
            for index, (stored_stream, fields) in enumerate(self.messages[:count], start=1)
            if stored_stream == stream
        ]

    async def xdel(self, stream, message_id):
        self.deleted.append((stream, message_id))

    async def xpending(self, stream, group):
        return {"pending": len(self.pending)}

    async def xrevrange(self, stream, *, count):
        return [("1-0", {})] if self.messages else []

    async def xlen(self, stream):
        return sum(1 for stored_stream, _ in self.messages if stored_stream == stream)

    async def zcard(self, key):
        return len(self.zset)


@pytest.mark.asyncio
async def test_redis_stream_publish_is_idempotent_by_outbox_id():
    redis = FakeRedis()
    publisher = RedisEventPublisher(redis, stream="harness:events", dedupe_ttl_seconds=60)
    event = Event(event_type="workflow.completed", payload={"workflow_id": "one"})

    await publisher.publish("outbox-1", event)
    await publisher.publish("outbox-1", event)

    assert len(redis.messages) == 1
    assert redis.messages[0][1]["event_id"] == "outbox-1"


@pytest.mark.asyncio
async def test_redis_consumer_group_processes_new_messages():
    redis = FakeRedis()
    handled = []
    redis.new_messages.append((
        "1-0",
        {
            "event_id": "outbox-1",
            "event_type": "workflow.completed",
            "session_id": "session",
            "source": "system",
            "payload": '{"workflow_id": "one"}',
        },
    ))
    consumer = RedisStreamConsumer(
        redis,
        stream="harness:events",
        group="projection",
        consumer="worker-1",
        handler=lambda event: _handle(handled, event),
    )

    stats = await consumer.drain_once()

    assert stats.processed == 1
    assert handled[0].event_type == "workflow.completed"
    assert redis.acked == [("harness:events", "projection", "1-0")]


@pytest.mark.asyncio
async def test_redis_consumer_retries_then_dlq():
    redis = FakeRedis()
    redis.new_messages.append((
        "1-0",
        {
            "event_id": "outbox-1",
            "event_type": "workflow.failed",
            "payload": "{}",
            "attempts": "1",
        },
    ))
    consumer = RedisStreamConsumer(
        redis,
        stream="harness:events",
        group="projection",
        consumer="worker-1",
        handler=_fail,
        max_retries=1,
    )

    stats = await consumer.drain_once()

    assert stats.dlq == 1
    assert redis.messages[0][0] == "harness:events:dlq"
    assert redis.messages[0][1]["attempts"] == "2"


@pytest.mark.asyncio
async def test_redis_dlq_replay_moves_entries_to_target_stream():
    redis = FakeRedis()
    await redis.xadd("harness:events:dlq", {"event_type": "x", "payload": "{}"}, maxlen=1, approximate=True)

    replayed = await replay_dlq(
        redis,
        dlq_stream="harness:events:dlq",
        target_stream="harness:events",
        count=10,
    )

    assert replayed == 1
    assert redis.messages[-1][0] == "harness:events"
    assert redis.deleted == [("harness:events:dlq", "1-0")]


@pytest.mark.asyncio
async def test_redis_consumer_metrics_snapshot_exposes_slo_inputs():
    redis = FakeRedis()
    redis.pending.append({"message_id": "1-0", "time_since_delivered": 60_000})
    await redis.xadd("harness:events", {"event_type": "x"}, maxlen=1, approximate=True)
    await redis.xadd("harness:events:dlq", {"event_type": "x"}, maxlen=1, approximate=True)
    redis.zset["retry"] = 1
    consumer = RedisStreamConsumer(
        redis,
        stream="harness:events",
        group="projection",
        consumer="worker-1",
        handler=lambda event: _handle([], event),
    )

    snapshot = await consumer.metrics_snapshot()

    assert snapshot["stream_backlog"] == 1
    assert snapshot["pending"] == 1
    assert snapshot["retry_backlog"] == 1
    assert snapshot["dlq_backlog"] == 1


async def _handle(handled, event):
    handled.append(event)


async def _fail(event):
    raise RuntimeError("projection failed")
