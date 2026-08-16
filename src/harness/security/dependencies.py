"""FastAPI authorization dependencies."""

from collections.abc import Callable

from fastapi import HTTPException, Request

from harness.security.models import ActorContext


def get_current_actor(request: Request) -> ActorContext:
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return actor


def require_permission(permission: str) -> Callable:
    def dependency(request: Request) -> ActorContext:
        resolved = get_current_actor(request)
        if not resolved.has_permission(permission):
            raise HTTPException(
                status_code=403,
                detail=f"Permission '{permission}' is required",
            )
        return resolved

    return dependency
