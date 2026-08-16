"""Durable workflow query and lifecycle routes."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from harness.security.dependencies import require_permission
from harness.security.models import ActorContext

router = APIRouter()


@router.get("")
async def list_workflows(
    request: Request,
    state: str | None = None,
    session_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: ActorContext = Depends(require_permission("workflow:read")),
):
    workflows = await request.app.state.workflow_service.list(
        state=state,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )
    return {"workflows": workflows, "count": len(workflows)}


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    request: Request,
    _: ActorContext = Depends(require_permission("workflow:read")),
):
    workflow = await request.app.state.workflow_service.get(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    temporal_client = getattr(request.app.state, "temporal_approval_client", None)
    if (
        request.app.state.settings.temporal_authoritative_execution
        and temporal_client is not None
        and workflow.get("execution_engine") == "temporal"
    ):
        try:
            workflow["temporal"] = await temporal_client.query_agent(workflow_id)
        except Exception:
            workflow["temporal"] = {"state": workflow.get("temporal_state", "unknown")}
    return workflow


@router.post("/{workflow_id}/resume")
async def resume_workflow(
    workflow_id: str,
    request: Request,
    _: ActorContext = Depends(require_permission("workflow:resume")),
):
    grant = await request.app.state.human_task_service.get_active_grant(workflow_id)
    if grant is None:
        raise HTTPException(
            status_code=409,
            detail="Workflow has no active approval grant",
        )
    try:
        await request.app.state.workflow_service.request_resume(
            workflow_id,
            reason="manual resume requested",
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Workflow not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"workflow_id": workflow_id, "state": "resume_requested"}


@router.post("/{workflow_id}/cancel")
async def cancel_workflow(
    workflow_id: str,
    body: dict,
    request: Request,
    _: ActorContext = Depends(require_permission("workflow:cancel")),
):
    temporal_client = getattr(request.app.state, "temporal_approval_client", None)
    if (
        request.app.state.settings.temporal_authoritative_execution
        and temporal_client is not None
    ):
        try:
            await temporal_client.cancel_agent(workflow_id)
        except Exception:
            pass
    if not await request.app.state.workflow_service.cancel(
        workflow_id,
        reason=body.get("reason", "cancelled by operator"),
    ):
        raise HTTPException(
            status_code=409,
            detail="Workflow not found or already terminal",
        )
    return {"workflow_id": workflow_id, "state": "cancelled"}
