# SQLite to PostgreSQL Migration Runbook

1. Stop API writes and let the Outbox drain. Keep Temporal Workers running only
   for already-started Activities; do not start new legacy workflows.
2. Copy local CAS artifact blobs to MinIO and verify each destination SHA-256.
   The database migration retains `artifact_versions.storage_key` and does not
   move blobs itself.
3. Run `harness-migrate-sqlite data/harness.db postgresql://... --dry-run`.
   It validates source/target table counts without copying rows.
4. Run the command again without `--dry-run`. It inserts rows in foreign-key
   order, is idempotent through `ON CONFLICT DO NOTHING`, fixes the outbox
   sequence, and fails on count mismatch.
5. Start API and Temporal Workers with `HARNESS_DATABASE_URL` set to PostgreSQL
   in read-only/shadow mode. Compare workflow, approval, audit, and artifact
   references before enabling writes.
6. Enable Temporal authoritative execution and disable the legacy ResumeWorker
   only when all non-terminal legacy runs are recovered or explicitly marked
   `recovery_required`. Retain the SQLite file read-only for audit rollback.
