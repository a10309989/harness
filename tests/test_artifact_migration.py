import hashlib
from datetime import datetime

import pytest

from harness.artifacts.migration import ArtifactMigrator
from harness.artifacts.storage import DualReadStorage, StorageBackend


class MemoryStorage(StorageBackend):
    def __init__(self) -> None:
        self.objects = {}

    async def put(self, content: bytes) -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        key = f"sha256/{digest[:2]}/{digest[2:4]}/{digest}"
        self.objects[key] = content
        return digest, key

    async def get(self, storage_key: str, expected_digest: str) -> bytes:
        content = self.objects[storage_key]
        if hashlib.sha256(content).hexdigest() != expected_digest:
            raise IOError("digest mismatch")
        return content


@pytest.mark.asyncio
async def test_artifact_migrator_switches_key_only_after_verified_copy(db):
    source = MemoryStorage()
    target = MemoryStorage()
    content = b"artifact-content"
    digest, source_key = await source.put(content)
    created_at = datetime(2026, 1, 1)
    await db.execute(
        """INSERT INTO artifacts
           (id, tenant_id, artifact_type, name, status, current_version_id, created_by, created_at)
           VALUES ($1, 'default', 'generic_document', 'artifact', 'active', $2, 'actor', $3)""",
        ("artifact-1", "version-1", created_at),
    )
    await db.execute(
        """INSERT INTO artifact_versions
           (id, artifact_id, version_number, content_digest, storage_key,
            media_type, size_bytes, metadata, created_by, trace_id, created_at)
           VALUES ($1, $2, 1, $3, $4, 'text/plain', $5, '{}', 'actor', 'trace', $6)""",
        ("version-1", "artifact-1", digest, source_key, len(content), created_at),
    )
    await db.commit()
    migrator = ArtifactMigrator(db, source, target)

    report = await migrator.run()
    row = await db.fetch_one("SELECT storage_key FROM artifact_versions WHERE id = $1", ("version-1",))

    assert report.verified is True
    assert report.copied == 1
    assert report.switched == 1
    assert await target.get(row["storage_key"], digest) == content


@pytest.mark.asyncio
async def test_dual_read_storage_falls_back_to_legacy_store():
    primary = MemoryStorage()
    fallback = MemoryStorage()
    content = b"legacy-only"
    digest, key = await fallback.put(content)
    dual = DualReadStorage(primary, fallback)

    assert await dual.get(key, digest) == content
