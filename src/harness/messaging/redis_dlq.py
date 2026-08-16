"""Replay Redis Streams DLQ entries back to the realtime stream."""

from __future__ import annotations

import argparse
import asyncio


async def replay_dlq(
    client,
    *,
    dlq_stream: str,
    target_stream: str,
    count: int = 100,
) -> int:
    entries = await client.xrange(dlq_stream, min="-", max="+", count=count)
    replayed = 0
    for message_id, fields in entries:
        replay_fields = dict(fields)
        replay_fields.pop("failed_at_ms", None)
        replay_fields.pop("last_error", None)
        replay_fields["replayed_from_dlq_id"] = message_id
        await client.xadd(
            target_stream,
            replay_fields,
            maxlen=10_000,
            approximate=True,
        )
        await client.xdel(dlq_stream, message_id)
        replayed += 1
    return replayed


async def _main_async() -> None:
    from redis.asyncio import Redis

    parser = argparse.ArgumentParser(description="Replay Harness Redis DLQ entries")
    parser.add_argument("--redis-url", required=True)
    parser.add_argument("--dlq-stream", default="harness:events:dlq")
    parser.add_argument("--target-stream", default="harness:events")
    parser.add_argument("--count", type=int, default=100)
    args = parser.parse_args()

    client = Redis.from_url(args.redis_url, decode_responses=True)
    try:
        replayed = await replay_dlq(
            client,
            dlq_stream=args.dlq_stream,
            target_stream=args.target_stream,
            count=args.count,
        )
        print(f"replayed={replayed}")
    finally:
        await client.aclose()


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
