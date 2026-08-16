"""CLI for verified LocalCAS to MinIO artifact migration."""

from __future__ import annotations

import argparse
import asyncio

from harness.artifacts.migration import ArtifactMigrator
from harness.artifacts.storage import LocalCAS, MinIOStorage
from harness.db import create_database
from harness.runtime.settings import RuntimeSettings


async def _main_async() -> None:
    parser = argparse.ArgumentParser(description="Migrate Harness artifacts to MinIO")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    settings = RuntimeSettings()
    db = create_database(settings.database_url)
    await db.initialize()
    try:
        migrator = ArtifactMigrator(
            db,
            LocalCAS(settings.artifact_storage_path),
            MinIOStorage(
                settings.minio_endpoint,
                settings.minio_bucket,
                settings.minio_access_key,
                settings.minio_secret_key,
                secure=settings.minio_secure,
            ),
        )
        report = await migrator.run(dry_run=args.dry_run, limit=args.limit)
        print(
            "checked={checked} copied={copied} switched={switched} "
            "skipped={skipped} failed={failed}".format(
                checked=report.checked,
                copied=report.copied,
                switched=report.switched,
                skipped=report.skipped,
                failed=len(report.failed),
            )
        )
        if report.failed:
            raise SystemExit(1)
    finally:
        await db.close()


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
