"""Production operations summary endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from harness.security.dependencies import require_permission
from harness.security.models import ActorContext

router = APIRouter()


@router.get("/summary")
async def operations_summary(
    request: Request,
    _: ActorContext = Depends(require_permission("ops:read")),
):
    db = request.app.state.database
    settings = request.app.state.settings
    return {
        "outbox": await _counts(db, "outbox_events", "status"),
        "runner": await _counts(db, "runner_tasks", "status"),
        "redis": {
            "enabled": settings.redis_events_enabled,
            "stream": settings.redis_event_stream,
            "consumer_group": settings.redis_event_consumer_group,
            "dlq_stream": settings.redis_event_dlq_stream,
            "max_retries": settings.redis_event_max_retries,
        },
        "runner_manager": {
            "url": settings.runner_manager_url,
            "max_concurrent_tasks": settings.runner_max_concurrent_tasks,
            "egress_policy": settings.runner_egress_policy,
            "seccomp_profile": settings.runner_seccomp_profile,
            "apparmor_profile": settings.runner_apparmor_profile or None,
            "signature_required": settings.runner_signature_required,
        },
        "backup": {
            "rpo_minutes": settings.backup_rpo_minutes,
            "rto_minutes": settings.backup_rto_minutes,
        },
        "otel": {
            "enabled": settings.otel_enabled,
            "service_name": settings.otel_service_name,
            "endpoint": settings.otel_exporter_otlp_endpoint,
        },
    }


async def _counts(db, table: str, column: str) -> dict[str, int]:
    try:
        rows = await db.fetch_all(
            f"SELECT {column} AS key, COUNT(*) AS count FROM {table} GROUP BY {column}"
        )
    except Exception:
        return {}
    return {row["key"]: row["count"] for row in rows}
