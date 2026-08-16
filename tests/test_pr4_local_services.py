import os

import pytest

from harness.artifacts.storage import MinIOStorage
from harness.core.events import Event
from harness.messaging.redis_events import RedisEventPublisher
from harness.runtime.settings import RuntimeSettings


pytestmark = pytest.mark.skipif(
    os.getenv("HARNESS_RUN_LOCAL_SERVICES") != "1",
    reason="requires the local Docker MinIO and Redis services",
)


async def test_local_minio_and_redis_services():
    settings = RuntimeSettings()
    storage = MinIOStorage(
        settings.minio_endpoint,
        settings.minio_bucket,
        settings.minio_access_key,
        settings.minio_secret_key,
        secure=settings.minio_secure,
    )
    digest, storage_key = await storage.put(b"pr4-local-service-check")
    assert await storage.get(storage_key, digest) == b"pr4-local-service-check"

    publisher = await RedisEventPublisher.connect(settings)
    try:
        await publisher.publish("pr4-local-service-check", Event(event_type="pr4.checked"))
        entries = await publisher._client.xrevrange(settings.redis_event_stream, count=1)
        assert entries[0][1]["event_id"] == "pr4-local-service-check"
    finally:
        await publisher.close()
