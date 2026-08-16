# Enterprise Operations Runbook

## Runner Manager

- API and Temporal Workers must submit execution work through `RunnerClient`.
- Only `harness-runner-manager` may mount `/var/run/docker.sock`.
- Runner images must be pinned by digest and listed in `HARNESS_RUNNER_ALLOWED_IMAGE_DIGESTS`.
- The manager enforces `seccomp`, optional AppArmor, no-new-privileges, no network egress by default, pids, CPU, memory, and concurrent task limits.
- Image scanning is an admission hook in `RunnerManager`; production deployments should wire it to Trivy, Grype, or the enterprise registry scanner.

## Redis Realtime Plane

- PostgreSQL outbox remains the event source of truth.
- Redis Streams are realtime projections for SSE/WebSocket consumers only.
- Consumer groups must monitor `stream_backlog`, `pending`, `retry_backlog`, `dlq_backlog`, and `consumer_lag_ms`.
- Alert when DLQ is nonzero for 5 minutes, consumer lag exceeds the realtime SLO, or retry backlog grows for 3 consecutive windows.
- Replay DLQ entries with `harness-redis-dlq-replay --redis-url redis://... --count 100` after fixing the projection handler.

## Artifact Migration

- Run `harness-migrate-artifacts-minio --dry-run` before switching storage.
- The migrator copies LocalCAS content to MinIO and reads it back by digest before updating `artifact_versions.storage_key`.
- Use `DualReadStorage` during migration windows so MinIO misses fall back to LocalCAS.
- Configure MinIO bucket versioning, server-side encryption, lifecycle retention, access policies, and object-lock settings outside the application.
- Back up both PostgreSQL metadata and MinIO objects before changing `HARNESS_ARTIFACT_STORAGE_BACKEND=minio`.

## Gateway And Identity

- Put the API behind an external gateway that terminates OIDC/SSO and mTLS.
- Enable `HARNESS_GATEWAY_EXTERNAL_REQUIRED=true` so API requests require trusted OIDC and mTLS headers.
- Enforce rate limits, quota, and WAF policy at the gateway; pass the quota subject and WAF verdict headers to the API.
- Preserve `traceparent` and `X-Trace-ID` across Gateway, API, Temporal, Redis, Runner, LLM, and MCP calls.

## Backup And Restore Drill

- PostgreSQL: continuous WAL archiving plus daily logical dumps; target `HARNESS_BACKUP_RPO_MINUTES`.
- MinIO: bucket replication or object backup with versioning enabled; verify object digest after restore.
- Temporal: back up persistence database and namespace configuration; restore into an isolated namespace before promotion.
- Redis: no business-state restore requirement; validate realtime consumers rebuild projections from PostgreSQL outbox.
- RTO gate: restore PostgreSQL, MinIO, Temporal, start workers, replay DLQ, and pass smoke tests within `HARNESS_BACKUP_RTO_MINUTES`.

## Release Gates

- Full unit suite must pass.
- PostgreSQL integration suite must pass against an empty migrated schema.
- Runner Manager smoke test must pass with a digest-pinned image.
- Artifact migration dry-run must report zero failures.
- Load test must meet API latency, Redis consumer lag, Temporal task queue latency, and Runner queue depth SLOs.
