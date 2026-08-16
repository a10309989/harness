"""Identity and credential API routes."""

from fastapi import APIRouter, Depends, HTTPException, Request

from harness.observability.audit import record_audit
from harness.security.dependencies import get_current_actor, require_permission
from harness.security.models import ActorContext, ActorType

router = APIRouter()


@router.get("/me")
async def get_my_identity(actor: ActorContext = Depends(get_current_actor)):
    return actor.to_dict()


@router.post("/api-keys")
async def create_api_key(
    body: dict,
    request: Request,
    _: ActorContext = Depends(require_permission("identity:manage")),
):
    actor_id = (body.get("actor_id") or "").strip()
    if not actor_id:
        raise HTTPException(status_code=400, detail="actor_id is required")
    raw_key, metadata = await request.app.state.auth_service.create_api_key(actor_id)
    await record_audit(
        "identity.credential_created",
        resource_type="api_credential",
        resource_id=metadata["id"],
        metadata={"actor_id": actor_id, "key_prefix": metadata["key_prefix"]},
    )
    return {
        **metadata,
        "api_key": raw_key,
        "warning": "This key is returned once and cannot be recovered.",
    }


@router.delete("/api-keys/{credential_id}")
async def revoke_api_key(
    credential_id: str,
    request: Request,
    _: ActorContext = Depends(require_permission("identity:manage")),
):
    if not await request.app.state.auth_service.revoke_api_key(credential_id):
        raise HTTPException(status_code=404, detail="API credential not found")
    await record_audit(
        "identity.credential_revoked",
        resource_type="api_credential",
        resource_id=credential_id,
    )
    return {"id": credential_id, "revoked": True}


@router.get("/actors")
async def list_actors(
    request: Request,
    _: ActorContext = Depends(require_permission("identity:manage")),
):
    actors = await request.app.state.auth_service.list_actors()
    return {"actors": actors, "count": len(actors)}


@router.post("/actors")
async def create_actor(
    body: dict,
    request: Request,
    _: ActorContext = Depends(require_permission("identity:manage")),
):
    actor_id = (body.get("actor_id") or "").strip()
    display_name = (body.get("display_name") or actor_id).strip()
    if not actor_id:
        raise HTTPException(status_code=400, detail="actor_id is required")
    try:
        actor_type = ActorType(body.get("actor_type", "user"))
        actor = await request.app.state.auth_service.create_actor(
            actor_id=actor_id,
            actor_type=actor_type,
            display_name=display_name,
            tenant_id=(body.get("tenant_id") or "default").strip(),
            roles=tuple(body.get("roles", [])),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await record_audit(
        "identity.actor_created",
        resource_type="actor",
        resource_id=actor_id,
        input_data=actor.to_dict(),
    )
    return actor.to_dict()


@router.put("/actors/{actor_id}/roles")
async def update_actor_roles(
    actor_id: str,
    body: dict,
    request: Request,
    _: ActorContext = Depends(require_permission("identity:manage")),
):
    try:
        actor = await request.app.state.auth_service.set_actor_roles(
            actor_id,
            tuple(body.get("roles", [])),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await record_audit(
        "identity.actor_roles_updated",
        resource_type="actor",
        resource_id=actor_id,
        input_data={"roles": list(actor.roles)},
    )
    return actor.to_dict()
