"""Knowledge base API routes."""

from fastapi import APIRouter, Depends

from harness.api.deps import get_knowledge_service
from harness.knowledge.service import KnowledgeService
from harness.models.knowledge import (
    AgentKnowledgeProfile,
    KnowledgeCollectionDraft,
    KnowledgeDocumentDraft,
    KnowledgeSearchRequest,
)
from harness.security.dependencies import require_permission

router = APIRouter()


@router.get("/collections")
async def list_collections(
    service: KnowledgeService = Depends(get_knowledge_service),
    _: object = Depends(require_permission("knowledge:read")),
):
    """List tenant-visible enterprise knowledge collections."""
    collections = await service.list_collections()
    return {"collections": collections, "count": len(collections)}


@router.put("/collections/{name}")
async def upsert_collection(
    name: str,
    body: dict,
    service: KnowledgeService = Depends(get_knowledge_service),
    _: object = Depends(require_permission("knowledge:write")),
):
    """Create or update a knowledge collection."""
    collection = await service.upsert_collection(
        KnowledgeCollectionDraft(
            name=name,
            description=body.get("description", ""),
            metadata=body.get("metadata", {}),
        )
    )
    return {"collection": collection}


@router.get("/documents")
async def list_documents(
    collection: str | None = None,
    service: KnowledgeService = Depends(get_knowledge_service),
    _: object = Depends(require_permission("knowledge:read")),
):
    """List documents, optionally scoped to a collection."""
    documents = await service.list_documents(collection)
    return {"documents": documents, "count": len(documents)}


@router.get("/documents/{document_id}")
async def get_document(
    document_id: str,
    service: KnowledgeService = Depends(get_knowledge_service),
    _: object = Depends(require_permission("knowledge:read")),
):
    """Get document details and chunk previews."""
    document = await service.get_document(document_id)
    return {"document": document}


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    service: KnowledgeService = Depends(get_knowledge_service),
    _: object = Depends(require_permission("knowledge:write")),
):
    """Soft-delete a knowledge document from lists and retrieval."""
    deleted = await service.delete_document(document_id)
    return {"deleted": deleted, "document_id": document_id}


@router.post("/search")
async def search_knowledge(
    body: dict,
    service: KnowledgeService = Depends(get_knowledge_service),
    _: object = Depends(require_permission("knowledge:read")),
):
    """Search knowledge bases with agent profile-aware retrieval policy."""
    request = KnowledgeSearchRequest.model_validate(body)
    return await service.search(request)


@router.post("/ingest")
async def ingest_knowledge(
    body: dict,
    service: KnowledgeService = Depends(get_knowledge_service),
    _: object = Depends(require_permission("knowledge:write")),
):
    """Ingest inline knowledge content into a collection."""
    result = await service.ingest_document(KnowledgeDocumentDraft.model_validate(body))
    return {"message": "Knowledge ingested", **result}


@router.post("/artifacts/{version_id}/index")
async def index_artifact_version(
    version_id: str,
    body: dict,
    service: KnowledgeService = Depends(get_knowledge_service),
    _: object = Depends(require_permission("knowledge:write")),
):
    """Index an immutable artifact version into a knowledge collection."""
    result = await service.write_artifact_version_to_knowledge(
        version_id,
        collection_name=body.get("collection_name", "agent_artifacts"),
        title=body.get("title"),
        metadata=body.get("metadata", {}),
    )
    return {"message": "Artifact indexed", **result}


@router.get("/agent-profiles")
async def list_agent_profiles(
    service: KnowledgeService = Depends(get_knowledge_service),
    _: object = Depends(require_permission("knowledge:read")),
):
    """List agent-scoped knowledge retrieval profiles."""
    profiles = await service.list_agent_profiles()
    return {
        "profiles": [profile.model_dump(mode="json") for profile in profiles],
        "count": len(profiles),
    }


@router.get("/agent-profiles/{agent_id}")
async def get_agent_profile(
    agent_id: str,
    service: KnowledgeService = Depends(get_knowledge_service),
    _: object = Depends(require_permission("knowledge:read")),
):
    """Get a single agent knowledge profile."""
    profile = await service.get_agent_profile(agent_id)
    return {"profile": profile.model_dump(mode="json") if profile else None}


@router.put("/agent-profiles/{agent_id}")
async def upsert_agent_profile(
    agent_id: str,
    body: dict,
    service: KnowledgeService = Depends(get_knowledge_service),
    _: object = Depends(require_permission("knowledge:write")),
):
    """Create or update an agent-scoped knowledge profile."""
    profile = AgentKnowledgeProfile.model_validate({"agent_id": agent_id, **body})
    saved = await service.upsert_agent_profile(profile)
    return {"profile": saved.model_dump(mode="json")}
