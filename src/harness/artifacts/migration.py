"""Artifact blob migration from LocalCAS to MinIO-compatible storage."""

from __future__ import annotations

from dataclasses import dataclass, field

from harness.artifacts.storage import StorageBackend
from harness.observability.otel import traced_span


@dataclass
class ArtifactMigrationReport:
    checked: int = 0
    copied: int = 0
    switched: int = 0
    skipped: int = 0
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        return not self.failed


class ArtifactMigrator:
    """Copies artifact blobs and updates storage keys only after digest verify."""

    def __init__(
        self,
        db,
        source: StorageBackend,
        target: StorageBackend,
    ) -> None:
        self.db = db
        self.source = source
        self.target = target

    async def run(self, *, dry_run: bool = False, limit: int | None = None) -> ArtifactMigrationReport:
        with traced_span("artifact.migration.run", dry_run=dry_run, limit=limit):
            rows = await self.db.fetch_all(
                """SELECT id, storage_key, content_digest
                   FROM artifact_versions ORDER BY id"""
            )
            report = ArtifactMigrationReport()
            for row in rows[:limit]:
                report.checked += 1
                version_id = row["id"]
                digest = row["content_digest"]
                try:
                    if await self._target_verified(row["storage_key"], digest):
                        report.skipped += 1
                        continue
                    content = await self.source.get(row["storage_key"], digest)
                    new_digest, new_key = await self.target.put(content)
                    if new_digest != digest:
                        raise IOError("Target digest differs from catalog digest")
                    verified = await self.target.get(new_key, digest)
                    if verified != content:
                        raise IOError("Target content differs after copy")
                    report.copied += 1
                    if not dry_run:
                        await self._switch_storage_key(version_id, digest, new_key)
                        report.switched += 1
                except Exception as exc:
                    report.failed[version_id] = str(exc)
            return report

    async def _target_verified(self, storage_key: str, digest: str) -> bool:
        try:
            await self.target.get(storage_key, digest)
            return True
        except Exception:
            return False

    async def _switch_storage_key(
        self,
        version_id: str,
        digest: str,
        storage_key: str,
    ) -> None:
        await self.db.execute(
            """UPDATE artifact_versions SET storage_key = $1
               WHERE id = $2 AND content_digest = $3""",
            (storage_key, version_id, digest),
        )
        await self.db.commit()
