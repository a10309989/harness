"""Audit event query and integrity verification routes."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from harness.security.dependencies import require_permission
from harness.security.models import ActorContext

router = APIRouter()


@router.get("/events")
async def list_audit_events(
    request: Request,
    trace_id: str | None = None,
    session_id: str | None = None,
    actor_id: str | None = None,
    event_type: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    decision: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: ActorContext = Depends(require_permission("audit:read")),
):
    events = await request.app.state.audit_service.list_events(
        trace_id=trace_id,
        session_id=session_id,
        actor_id=actor_id,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        decision=decision,
        limit=limit,
        offset=offset,
    )
    return {"events": events, "count": len(events), "limit": limit, "offset": offset}


@router.get("/events/{event_id}")
async def get_audit_event(
    event_id: str,
    request: Request,
    _: ActorContext = Depends(require_permission("audit:read")),
):
    event = await request.app.state.audit_service.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Audit event not found")
    return event


@router.get("/traces/{trace_id}")
async def get_trace(
    trace_id: str,
    request: Request,
    _: ActorContext = Depends(require_permission("audit:read")),
):
    events = await request.app.state.audit_service.list_events(
        trace_id=trace_id,
        limit=500,
    )
    events.reverse()
    return {"trace_id": trace_id, "events": events, "count": len(events)}


@router.get("/verify")
async def verify_audit_chain(
    request: Request,
    actor: ActorContext = Depends(require_permission("audit:read")),
):
    return await request.app.state.audit_service.verify_chain(actor.tenant_id)
