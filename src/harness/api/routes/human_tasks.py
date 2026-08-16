"""Human approval queue and task action routes."""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from harness.security.dependencies import require_permission
from harness.security.models import ActorContext

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("")
async def list_human_tasks(
    request: Request,
    state: str | None = None,
    workflow_id: str | None = None,
    session_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: ActorContext = Depends(require_permission("human_task:read")),
):
    tasks = await request.app.state.human_task_service.list(
        state=state,
        workflow_id=workflow_id,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )
    return {"tasks": tasks, "count": len(tasks)}


@router.get("/{task_id}")
async def get_human_task(
    task_id: str,
    request: Request,
    _: ActorContext = Depends(require_permission("human_task:read")),
):
    task = await request.app.state.human_task_service.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Human task not found")
    return task


@router.post("/{task_id}/claim")
async def claim_human_task(
    task_id: str,
    body: dict,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    _: ActorContext = Depends(require_permission("human_task:claim")),
):
    return await _perform(
        request.app.state.human_task_service.claim(
            task_id,
            idempotency_key=idempotency_key,
            lease_minutes=int(body.get("lease_minutes", 15)),
        )
    )


@router.post("/{task_id}/release")
async def release_human_task(
    task_id: str,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    _: ActorContext = Depends(require_permission("human_task:claim")),
):
    return await _perform(
        request.app.state.human_task_service.release(
            task_id,
            idempotency_key=idempotency_key,
        )
    )


@router.post("/{task_id}/approve")
async def approve_human_task(
    task_id: str,
    body: dict,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    _: ActorContext = Depends(require_permission("human_task:approve")),
):
    task = await _perform(
        request.app.state.human_task_service.approve(
            task_id,
            comment=body.get("comment", ""),
            idempotency_key=idempotency_key,
        )
    )
    if task["state"] == "approved":
        grant = await request.app.state.human_task_service.get_active_grant(task["workflow_id"])
        temporal_client = getattr(request.app.state, "temporal_approval_client", None)
        if temporal_client is not None and grant is not None:
            try:
                await temporal_client.approve(
                    workflow_id=task["workflow_id"],
                    task_id=task_id,
                    actor_id=request.state.actor.actor_id,
                    grant_id=grant["id"],
                )
            except Exception:
                logger.exception(
                    "Unable to mirror approval to Temporal for workflow %s",
                    task["workflow_id"],
                )
    return task


@router.post("/{task_id}/reject")
async def reject_human_task(
    task_id: str,
    body: dict,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    _: ActorContext = Depends(require_permission("human_task:approve")),
):
    task = await _perform(
        request.app.state.human_task_service.reject(
            task_id,
            comment=body.get("comment", ""),
            idempotency_key=idempotency_key,
        )
    )
    temporal_client = getattr(request.app.state, "temporal_approval_client", None)
    if temporal_client is not None:
        try:
            await temporal_client.reject(
                workflow_id=task["workflow_id"],
                task_id=task_id,
                actor_id=request.state.actor.actor_id,
                reason=body.get("comment", ""),
            )
        except Exception:
            logger.exception(
                "Unable to mirror rejection to Temporal for workflow %s",
                task["workflow_id"],
            )
    return task


async def _perform(awaitable):
    try:
        return await awaitable
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
