"""Immutable artifact catalog, content, and lineage routes."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from harness.models.artifact import ArtifactDraft, ArtifactType
from harness.security.dependencies import require_permission
from harness.security.models import ActorContext

router = APIRouter()


@router.get("")
async def list_artifacts(
    request: Request,
    artifact_type: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: ActorContext = Depends(require_permission("artifact:read")),
):
    artifacts = await request.app.state.artifact_service.list(
        artifact_type=artifact_type,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"artifacts": artifacts, "count": len(artifacts)}


@router.post("")
async def create_artifact(
    body: dict,
    request: Request,
    _: ActorContext = Depends(require_permission("artifact:write")),
):
    try:
        draft = ArtifactDraft(
            artifact_type=ArtifactType(body.get("artifact_type", "generic_document")),
            name=(body.get("name") or "").strip(),
            content=body.get("content", ""),
            media_type=body.get("media_type", "text/plain"),
            metadata=body.get("metadata", {}),
        )
        if not draft.name:
            raise ValueError("name is required")
        return (await request.app.state.artifact_service.create(draft)).model_dump()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{artifact_id}")
async def get_artifact(
    artifact_id: str,
    request: Request,
    actor: ActorContext = Depends(require_permission("artifact:read")),
):
    artifact = await request.app.state.artifact_service.get(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if (
        artifact["artifact_type"] == "workflow_checkpoint"
        and not actor.has_permission("workflow:read")
    ):
        raise HTTPException(status_code=403, detail="Workflow permission is required")
    return artifact


@router.post("/{artifact_id}/versions")
async def create_artifact_version(
    artifact_id: str,
    body: dict,
    request: Request,
    _: ActorContext = Depends(require_permission("artifact:write")),
):
    try:
        version = await request.app.state.artifact_service.create_version(
            artifact_id,
            body.get("content", ""),
            media_type=body.get("media_type", "text/plain"),
            metadata=body.get("metadata", {}),
        )
        return version.model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/versions/{version_id}/content")
async def read_artifact_version(
    version_id: str,
    request: Request,
    actor: ActorContext = Depends(require_permission("artifact:read")),
):
    database = request.app.state.database
    query = (
        """SELECT a.artifact_type
           FROM artifact_versions v
           JOIN artifacts a ON a.id = v.artifact_id
           WHERE v.id = $1"""
    )
    version_type = await request.app.state.database.fetch_one(
        query,
        (version_id,),
    )
    if version_type is None:
        raise HTTPException(status_code=404, detail="Artifact version not found")
    if (
        version_type["artifact_type"] == "workflow_checkpoint"
        and not actor.has_permission("workflow:read")
    ):
        raise HTTPException(status_code=403, detail="Workflow permission is required")
    try:
        version, content = await request.app.state.artifact_service.read_version(
            version_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IOError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return Response(
        content=content,
        media_type=version["media_type"],
        headers={
            "ETag": f"\"sha256:{version['content_digest']}\"",
            "X-Artifact-Digest": version["content_digest"],
        },
    )


@router.get("/versions/{version_id}/lineage")
async def get_artifact_lineage(
    version_id: str,
    request: Request,
    _: ActorContext = Depends(require_permission("artifact:read")),
):
    return await request.app.state.artifact_service.get_lineage(version_id)


@router.post("/links")
async def create_artifact_link(
    body: dict,
    request: Request,
    _: ActorContext = Depends(require_permission("artifact:write")),
):
    try:
        return await request.app.state.artifact_service.link(
            body["source_version_id"],
            body["target_version_id"],
            body.get("relationship", "derived_from"),
            body.get("metadata", {}),
        )
    except KeyError as exc:
        if exc.args and "version_id" in str(exc):
            raise HTTPException(status_code=400, detail="Version IDs are required")
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/{artifact_id}")
async def archive_artifact(
    artifact_id: str,
    request: Request,
    _: ActorContext = Depends(require_permission("artifact:archive")),
):
    if not await request.app.state.artifact_service.archive(artifact_id):
        raise HTTPException(status_code=404, detail="Active artifact not found")
    return {"artifact_id": artifact_id, "status": "archived"}
