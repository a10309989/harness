"""Full-chain trace search and viewer routes.

Traces are reconstructed from durable audit events (see harness.observability.trace)
so the whole chain — HTTP request -> workflow -> agent lifecycle -> LLM / tool
calls — can be located by session and inspected as a span tree.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from harness.security.dependencies import require_permission
from harness.security.models import ActorContext

router = APIRouter()


@router.get("")
async def list_traces(
    request: Request,
    session_id: str | None = None,
    workflow_id: str | None = None,
    event_type: str | None = None,
    status: str | None = None,
    agent_id: str | None = None,
    from_time: str | None = None,
    to_time: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: ActorContext = Depends(require_permission("trace:read")),
):
    return await request.app.state.trace_service.list_traces(
        session_id=session_id,
        workflow_id=workflow_id,
        event_type=event_type,
        status=status,
        agent_id=agent_id,
        from_time=from_time,
        to_time=to_time,
        limit=limit,
        offset=offset,
    )


@router.get("/by-session/{session_id}")
async def get_session_traces(
    session_id: str,
    request: Request,
    _: ActorContext = Depends(require_permission("trace:read")),
):
    traces = await request.app.state.trace_service.get_session_traces(session_id)
    return {"session_id": session_id, "traces": traces, "count": len(traces)}


@router.get("/flow/{workflow_id}")
async def get_flow(
    workflow_id: str,
    request: Request,
    _: ActorContext = Depends(require_permission("trace:read")),
):
    flow = await request.app.state.trace_service.get_flow(workflow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    return flow


@router.get("/{trace_id}")
async def get_trace(
    trace_id: str,
    request: Request,
    _: ActorContext = Depends(require_permission("trace:read")),
):
    trace = await request.app.state.trace_service.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace