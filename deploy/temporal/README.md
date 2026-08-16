# Harness Local Workflow Stack

This Compose stack runs PostgreSQL, Temporal Server, Temporal UI, Redis, and
MinIO for local Harness development.

## Start

```powershell
Copy-Item .env.example .env
docker compose --env-file .env -f docker-compose.yaml up -d
docker compose --env-file .env -f docker-compose.yaml ps
```

The same command starts the containerized Harness frontend (`http://127.0.0.1:5173`),
API (`http://127.0.0.1:8000`), and dedicated Temporal Worker. The frontend Nginx
container proxies `/api` and `/ws` to the API over the internal Docker network.
The API container owns the HTTP surface; the worker owns Temporal activity execution.
Only the optional `runner-manager` service is allowed to mount the Docker socket:

```powershell
docker compose --env-file .env -f docker-compose.yaml --profile runner up -d --build
```

Use the frontend URL for normal access. The API port remains loopback-bound for
local diagnostics, and can be placed behind `deploy/gateway` in production.

Temporal UI is available at `http://127.0.0.1:8233`. All service ports bind to
loopback only. Change values in `.env` before using this stack outside a local
developer workstation.

## Enable MinIO artifacts and Redis Streams

Start the API with the same MinIO credentials as `.env`:

```powershell
$env:HARNESS_ARTIFACT_STORAGE_BACKEND = "minio"
$env:HARNESS_MINIO_ACCESS_KEY = "harness-admin"
$env:HARNESS_MINIO_SECRET_KEY = "harness-minio-local"
$env:HARNESS_REDIS_EVENTS_ENABLED = "true"
```

Artifact catalog metadata remains in the application database. The durable
Outbox remains the event source of truth; Redis Streams receives an idempotent
realtime copy for consumer groups and UI fan-out.

## Start the Harness Temporal worker

Install the project dependencies, then start a dedicated worker process:

```powershell
$env:HARNESS_TEMPORAL_ENABLED = "true"
harness-temporal-worker
```

Set `HARNESS_TEMPORAL_ENABLED=true` on the API process to mirror new human
approval waits and decisions into Temporal during the shadow-migration phase.

## Build the isolated Runner image

```powershell
docker build -t harness-runner:local -f ..\runner\Dockerfile ..\..
```

`DockerRunner` only mounts a temporary read-only workspace and always runs
without network, Linux capabilities, host credentials, or the Docker socket.
Production must use an approved immutable image digest rather than this local
tag. Override `BASE_IMAGE` with an approved internal image mirror when needed.

## Enable authoritative approval resumption

Start the API with the following environment values only after shadow-mode
state comparison succeeds:

```powershell
$env:HARNESS_TEMPORAL_ENABLED = "true"
$env:HARNESS_TEMPORAL_AUTHORITATIVE_EXECUTION = "true"
$env:HARNESS_LEGACY_RESUME_WORKER_ENABLED = "false"
```

Keep `harness-temporal-worker` running as the dedicated worker. Alternatively,
set `HARNESS_TEMPORAL_WORKER_IN_API=true` to run the worker in the API process
for local development only. Do not enable both the legacy ResumeWorker and
authoritative Temporal execution: that would allow two schedulers to consume
the same approved workflow.

## PostgreSQL cutover

After a successful `harness-migrate-sqlite` count verification, configure both
the API and dedicated Temporal Worker with:

```powershell
$env:HARNESS_DATABASE_URL = "postgresql+asyncpg://harness:harness-local-dev@127.0.0.1:5432/harness"
$env:HARNESS_TEMPORAL_ENABLED = "true"
$env:HARNESS_TEMPORAL_AUTHORITATIVE_EXECUTION = "true"
$env:HARNESS_LEGACY_RESUME_WORKER_ENABLED = "false"
```

Keep the SQLite database read-only until workflow, approval, audit, and
artifact-reference shadow reads match PostgreSQL. The legacy Scheduler has no
durable execution ownership and must not be started after this cutover.

## Stop and reset

```powershell
docker compose --env-file .env -f docker-compose.yaml down
docker compose --env-file .env -f docker-compose.yaml down -v
```

The second command removes all local workflow, database, Redis, and artifact
data. Do not use it for an environment containing retained records.

## Service boundary

The Harness API and Worker join the control-plane network. A future Docker
Runner is separate and receives no PostgreSQL or Redis credentials. Do not
mount the Docker socket into the API container; only Runner Manager may create
one-shot execution containers.
