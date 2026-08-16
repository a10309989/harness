"""Enterprise knowledge service for agent-scoped retrieval and write-back."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from harness.artifacts.service import ArtifactService
from harness.db.protocols import DatabaseProtocol
from harness.models.artifact import ArtifactVersionRef
from harness.models.knowledge import (
    AgentKnowledgeProfile,
    KnowledgeCollectionDraft,
    KnowledgeDocumentDraft,
    KnowledgeSourceType,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
    RetrievalStrategy,
)
from harness.observability.audit import AuditService
from harness.observability.context import get_execution_context, new_id
from harness.observability.redaction import canonical_json


DEFAULT_AGENT_KNOWLEDGE_PROFILES: dict[str, AgentKnowledgeProfile] = {
    "remote.requirements": AgentKnowledgeProfile(
        agent_id="remote.requirements",
        collections=["requirements", "architecture", "agent_artifacts"],
        strategy=RetrievalStrategy.HYBRID,
        limit=8,
    ),
    "requirements_analyst": AgentKnowledgeProfile(
        agent_id="requirements_analyst",
        collections=["requirements", "architecture", "agent_artifacts"],
        strategy=RetrievalStrategy.HYBRID,
        limit=8,
    ),
    "remote.test_cases": AgentKnowledgeProfile(
        agent_id="remote.test_cases",
        collections=["requirements", "test_assets", "agent_artifacts"],
        strategy=RetrievalStrategy.HYBRID,
        limit=10,
    ),
    "test_case_generator": AgentKnowledgeProfile(
        agent_id="test_case_generator",
        collections=["requirements", "test_assets", "agent_artifacts"],
        strategy=RetrievalStrategy.HYBRID,
        limit=10,
    ),
    "remote.scripts": AgentKnowledgeProfile(
        agent_id="remote.scripts",
        collections=["api_specs", "automation_code", "test_assets", "agent_artifacts"],
        strategy=RetrievalStrategy.HYBRID,
        limit=10,
    ),
    "script_generator": AgentKnowledgeProfile(
        agent_id="script_generator",
        collections=["api_specs", "automation_code", "test_assets", "agent_artifacts"],
        strategy=RetrievalStrategy.HYBRID,
        limit=10,
    ),
    "remote.execution": AgentKnowledgeProfile(
        agent_id="remote.execution",
        collections=["test_assets", "runbooks", "automation_code"],
        strategy=RetrievalStrategy.KEYWORD,
        limit=8,
    ),
    "scheduler_executor": AgentKnowledgeProfile(
        agent_id="scheduler_executor",
        collections=["test_assets", "runbooks", "automation_code"],
        strategy=RetrievalStrategy.KEYWORD,
        limit=8,
    ),
    "remote.diagnosis": AgentKnowledgeProfile(
        agent_id="remote.diagnosis",
        collections=["observability", "incidents", "runbooks", "agent_artifacts"],
        strategy=RetrievalStrategy.HYBRID,
        limit=12,
    ),
    "log_analyst": AgentKnowledgeProfile(
        agent_id="log_analyst",
        collections=["observability", "incidents", "runbooks", "agent_artifacts"],
        strategy=RetrievalStrategy.HYBRID,
        limit=12,
    ),
    "remote.qa_conversation": AgentKnowledgeProfile(
        agent_id="remote.qa_conversation",
        collections=["requirements", "architecture", "runbooks", "agent_artifacts"],
        strategy=RetrievalStrategy.HYBRID,
        limit=6,
        write_back_artifacts=False,
    ),
}


DEFAULT_COLLECTIONS: tuple[KnowledgeCollectionDraft, ...] = (
    KnowledgeCollectionDraft(name="requirements", description="PRD, requirements, acceptance criteria"),
    KnowledgeCollectionDraft(name="architecture", description="Architecture, ADRs, API design documents"),
    KnowledgeCollectionDraft(name="api_specs", description="OpenAPI, protobuf, Postman and contract specs"),
    KnowledgeCollectionDraft(name="test_assets", description="Test strategy, test cases and test data"),
    KnowledgeCollectionDraft(name="automation_code", description="Automation framework, script templates and page objects"),
    KnowledgeCollectionDraft(name="runbooks", description="SOPs, deployment and incident runbooks"),
    KnowledgeCollectionDraft(name="observability", description="Logs, traces, metrics and alert excerpts"),
    KnowledgeCollectionDraft(name="incidents", description="Historical defects, incidents and postmortems"),
    KnowledgeCollectionDraft(name="agent_artifacts", description="Validated artifacts produced by agents"),
)


class KnowledgeService:
    def __init__(
        self,
        db: DatabaseProtocol,
        artifact_service: ArtifactService | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self.db = db
        self.artifact_service = artifact_service
        self.audit_service = audit_service

    async def initialize_defaults(self) -> None:
        for collection in DEFAULT_COLLECTIONS:
            await self.upsert_collection(collection)
        for profile in DEFAULT_AGENT_KNOWLEDGE_PROFILES.values():
            existing = await self.get_agent_profile(profile.agent_id)
            if existing is None:
                await self.upsert_agent_profile(profile)

    async def upsert_collection(self, draft: KnowledgeCollectionDraft) -> dict:
        context = get_execution_context()
        now = self._now()
        collection_id = self._stable_id("knowledge_collection", context.actor.tenant_id, draft.name)
        await self.db.execute(
            """INSERT INTO knowledge_collections
                   (id, tenant_id, name, description, metadata, created_by, created_at, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                   ON CONFLICT (tenant_id, name) DO UPDATE SET
                   description = EXCLUDED.description,
                   metadata = EXCLUDED.metadata,
                   updated_at = EXCLUDED.updated_at""",
            (
                collection_id,
                context.actor.tenant_id,
                draft.name,
                draft.description,
                canonical_json(draft.metadata),
                context.actor.actor_id,
                now,
                now,
            ),
        )
        await self.db.commit()
        return await self.get_collection(draft.name) or {}

    async def list_collections(self) -> list[dict]:
        context = get_execution_context()
        rows = await self.db.fetch_all(
            "SELECT * FROM knowledge_collections WHERE tenant_id = $1 ORDER BY name",
            (context.actor.tenant_id,),
        )
        return [self._json_row(row, "metadata") for row in rows]

    async def get_collection(self, name: str) -> dict | None:
        context = get_execution_context()
        row = await self.db.fetch_one(
            "SELECT * FROM knowledge_collections WHERE tenant_id = $1 AND name = $2",
            (context.actor.tenant_id, name),
        )
        return self._json_row(row, "metadata") if row else None

    async def list_documents(self, collection_name: str | None = None) -> list[dict]:
        context = get_execution_context()
        if collection_name:
            rows = await self.db.fetch_all(
                """SELECT d.*, c.name AS collection_name, COUNT(ch.id) AS chunk_count
                       FROM knowledge_documents d
                       JOIN knowledge_collections c ON c.id = d.collection_id
                       LEFT JOIN knowledge_chunks ch ON ch.document_id = d.id
                       WHERE d.tenant_id = $1 AND c.name = $2 AND d.status = 'active'
                       GROUP BY d.id, c.name
                       ORDER BY d.updated_at DESC""",
                (context.actor.tenant_id, collection_name),
            )
        else:
            rows = await self.db.fetch_all(
                """SELECT d.*, c.name AS collection_name, COUNT(ch.id) AS chunk_count
                       FROM knowledge_documents d
                       JOIN knowledge_collections c ON c.id = d.collection_id
                       LEFT JOIN knowledge_chunks ch ON ch.document_id = d.id
                       WHERE d.tenant_id = $1 AND d.status = 'active'
                       GROUP BY d.id, c.name
                       ORDER BY d.updated_at DESC""",
                (context.actor.tenant_id,),
            )
        return [self._document_row(row) for row in rows]

    async def get_document(self, document_id: str) -> dict | None:
        context = get_execution_context()
        row = await self.db.fetch_one(
            """SELECT d.*, c.name AS collection_name
                   FROM knowledge_documents d
                   JOIN knowledge_collections c ON c.id = d.collection_id
                   WHERE d.tenant_id = $1 AND d.id = $2 AND d.status = 'active'""",
            (context.actor.tenant_id, document_id),
        )
        if row is None:
            return None
        chunks = await self.db.fetch_all(
            """SELECT id, chunk_index, content, metadata, artifact_id, artifact_version_id, created_at
                   FROM knowledge_chunks
                   WHERE tenant_id = $1 AND document_id = $2
                   ORDER BY chunk_index""",
            (context.actor.tenant_id, document_id),
        )
        document = self._document_row(row)
        document["chunks"] = [
            {
                **dict(chunk),
                "metadata": self._loads(chunk["metadata"], {}),
            }
            for chunk in chunks
        ]
        return document

    async def delete_document(self, document_id: str) -> bool:
        context = get_execution_context()
        document = await self.db.fetch_one(
            """SELECT d.id, d.title, d.source_uri, c.name AS collection_name
                   FROM knowledge_documents d
                   JOIN knowledge_collections c ON c.id = d.collection_id
                   WHERE d.tenant_id = $1 AND d.id = $2 AND d.status = 'active'""",
            (context.actor.tenant_id, document_id),
        )
        if document is None:
            return False
        now = self._now()
        cursor = await self.db.execute(
            """UPDATE knowledge_documents
                   SET status = 'deleted', deleted_by = $1, deleted_at = $2, updated_at = $3
                   WHERE tenant_id = $4 AND id = $5 AND status = 'active'""",
            (
                context.actor.actor_id,
                now,
                now,
                context.actor.tenant_id,
                document_id,
            ),
        )
        await self.db.commit()
        deleted = cursor.rowcount > 0
        if deleted and self.audit_service:
            await self.audit_service.record(
                "knowledge.document.deleted",
                resource_type="knowledge_document",
                resource_id=document_id,
                metadata={
                    "title": document["title"],
                    "source_uri": document["source_uri"],
                    "collection_name": document["collection_name"],
                },
            )
        return deleted

    async def ingest_document(self, draft: KnowledgeDocumentDraft) -> dict:
        context = get_execution_context()
        collection = await self.upsert_collection(
            KnowledgeCollectionDraft(name=draft.collection_name)
        )
        now = self._now()
        content_digest = hashlib.sha256(draft.content.encode("utf-8")).hexdigest()
        source_uri = draft.source_uri or f"inline://{content_digest}"
        document_id = self._stable_id(
            "knowledge_document",
            context.actor.tenant_id,
            draft.collection_name,
            source_uri,
        )
        await self.db.execute(
            """INSERT INTO knowledge_documents
                   (id, tenant_id, collection_id, source_type, source_uri, title,
                    content_digest, status, metadata, acl_tags, created_by, created_at, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, 'active', $8, $9, $10, $11, $12)
                   ON CONFLICT (tenant_id, collection_id, source_uri) DO UPDATE SET
                   title = EXCLUDED.title,
                   content_digest = EXCLUDED.content_digest,
                   status = 'active',
                   deleted_by = NULL,
                   deleted_at = NULL,
                   metadata = EXCLUDED.metadata,
                   acl_tags = EXCLUDED.acl_tags,
                   updated_at = EXCLUDED.updated_at""",
            (
                document_id,
                context.actor.tenant_id,
                collection["id"],
                str(draft.source_type),
                source_uri,
                draft.title,
                content_digest,
                canonical_json(draft.metadata),
                canonical_json(draft.acl_tags),
                context.actor.actor_id,
                now,
                now,
            ),
        )
        await self._replace_chunks(document_id, collection["id"], draft, now)
        if draft.artifact_id and draft.artifact_version_id:
            await self._link_artifact_document(draft.artifact_id, draft.artifact_version_id, document_id, None)
        await self.db.commit()
        return {
            "document_id": document_id,
            "collection_name": draft.collection_name,
            "chunk_count": len(self._chunk_content(draft.content, draft.chunk_size)),
            "content_digest": content_digest,
        }

    async def write_artifact_to_knowledge(
        self,
        ref: ArtifactVersionRef,
        *,
        collection_name: str = "agent_artifacts",
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
        source_type: str = "agent_artifact",
    ) -> dict:
        if self.artifact_service is None:
            raise RuntimeError("ArtifactService is required for artifact knowledge write-back")
        version, content = await self.artifact_service.read_version(ref.version_id)
        return await self.ingest_document(
            KnowledgeDocumentDraft(
                collection_name=collection_name,
                title=title or f"Artifact {ref.artifact_id} v{ref.version_number}",
                content=content.decode("utf-8", errors="replace"),
                source_type=KnowledgeSourceType(source_type),
                source_uri=f"artifact://{ref.artifact_id}/{ref.version_id}",
                metadata={
                    **(metadata or {}),
                    "artifact": version,
                    "artifact_id": ref.artifact_id,
                    "artifact_version_id": ref.version_id,
                },
                artifact_id=ref.artifact_id,
                artifact_version_id=ref.version_id,
            )
        )

    async def write_artifact_version_to_knowledge(
        self,
        version_id: str,
        *,
        collection_name: str = "agent_artifacts",
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        if self.artifact_service is None:
            raise RuntimeError("ArtifactService is required for artifact knowledge write-back")
        version, _ = await self.artifact_service.read_version(version_id)
        ref = ArtifactVersionRef(
            artifact_id=version["artifact_id"],
            version_id=version["id"],
            version_number=version["version_number"],
            content_digest=version["content_digest"],
            media_type=version["media_type"],
            size_bytes=version["size_bytes"],
        )
        return await self.write_artifact_to_knowledge(
            ref,
            collection_name=collection_name,
            title=title,
            metadata=metadata,
        )

    async def search(self, request: KnowledgeSearchRequest) -> dict:
        context = get_execution_context()
        profile = await self.get_agent_profile(request.agent_id) if request.agent_id else None
        collections = request.collections or (profile.collections if profile else [])
        limit = min(max(request.limit or (profile.limit if profile else 8), 1), 50)
        strategy = request.strategy or (profile.strategy if profile else RetrievalStrategy.HYBRID)
        rows = await self._candidate_chunks(collections)
        query_terms = self._terms(request.query)
        results: list[KnowledgeSearchResult] = []
        filtered_count = 0
        for row in rows:
            row_acl = set(self._loads(row["acl_tags"], []))
            if request.acl_tags and row_acl and not row_acl.intersection(request.acl_tags):
                filtered_count += 1
                continue
            score = self._score(query_terms, row["content"], row["title"], strategy)
            if score <= 0:
                continue
            metadata = self._loads(row["chunk_metadata"], {})
            results.append(
                KnowledgeSearchResult(
                    chunk_id=row["chunk_id"],
                    document_id=row["document_id"],
                    collection_name=row["collection_name"],
                    title=row["title"],
                    content=row["content"],
                    score=score,
                    source_type=row["source_type"],
                    source_uri=row["source_uri"],
                    artifact_id=row["artifact_id"],
                    artifact_version_id=row["artifact_version_id"],
                    metadata=metadata,
                )
            )
        results.sort(key=lambda item: item.score, reverse=True)
        limited = results[:limit]
        await self._record_retrieval_audit(
            request=request,
            collection_names=collections,
            result_count=len(limited),
            filtered_count=filtered_count,
            source_refs=[result.model_dump(mode="json") for result in limited],
        )
        return {
            "query": request.query,
            "agent_id": request.agent_id,
            "strategy": str(strategy),
            "collections": collections,
            "results": [result.model_dump(mode="json") for result in limited],
            "count": len(limited),
            "filtered_count": filtered_count,
            "trace_id": context.trace_id,
        }

    async def get_agent_profile(self, agent_id: str | None) -> AgentKnowledgeProfile | None:
        if not agent_id:
            return None
        context = get_execution_context()
        row = await self.db.fetch_one(
            """SELECT * FROM agent_knowledge_profiles
                   WHERE tenant_id = $1 AND agent_id = $2""",
            (context.actor.tenant_id, agent_id),
        )
        if row is None:
            return None
        retrieval_policy = self._loads(row["retrieval_policy"], {})
        return AgentKnowledgeProfile(
            agent_id=row["agent_id"],
            collections=self._loads(row["collection_names"], []),
            strategy=retrieval_policy.get("strategy", RetrievalStrategy.HYBRID),
            limit=retrieval_policy.get("limit", 8),
            require_citations=retrieval_policy.get("require_citations", True),
            write_back_artifacts=retrieval_policy.get("write_back_artifacts", True),
            metadata=retrieval_policy.get("metadata", {}),
        )

    async def list_agent_profiles(self) -> list[AgentKnowledgeProfile]:
        context = get_execution_context()
        rows = await self.db.fetch_all(
            "SELECT agent_id FROM agent_knowledge_profiles WHERE tenant_id = $1 ORDER BY agent_id",
            (context.actor.tenant_id,),
        )
        profiles = [await self.get_agent_profile(row["agent_id"]) for row in rows]
        return [profile for profile in profiles if profile is not None]

    async def upsert_agent_profile(self, profile: AgentKnowledgeProfile) -> AgentKnowledgeProfile:
        context = get_execution_context()
        now = self._now()
        retrieval_policy = {
            "strategy": str(profile.strategy),
            "limit": profile.limit,
            "require_citations": profile.require_citations,
            "write_back_artifacts": profile.write_back_artifacts,
            "metadata": profile.metadata,
        }
        await self.db.execute(
            """INSERT INTO agent_knowledge_profiles
                   (tenant_id, agent_id, collection_names, retrieval_policy, updated_by, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6)
                   ON CONFLICT (tenant_id, agent_id) DO UPDATE SET
                   collection_names = EXCLUDED.collection_names,
                   retrieval_policy = EXCLUDED.retrieval_policy,
                   updated_by = EXCLUDED.updated_by,
                   updated_at = EXCLUDED.updated_at""",
            (
                context.actor.tenant_id,
                profile.agent_id,
                canonical_json(profile.collections),
                canonical_json(retrieval_policy),
                context.actor.actor_id,
                now,
            ),
        )
        await self.db.commit()
        if self.audit_service:
            await self.audit_service.record(
                "knowledge.agent_profile.updated",
                resource_type="agent_knowledge_profile",
                resource_id=profile.agent_id,
                metadata={"collections": profile.collections, "strategy": str(profile.strategy)},
            )
        return profile

    async def _replace_chunks(
        self,
        document_id: str,
        collection_id: str,
        draft: KnowledgeDocumentDraft,
        created_at,
    ) -> None:
        context = get_execution_context()
        await self.db.execute(
            "DELETE FROM knowledge_chunks WHERE document_id = $1",
            (document_id,),
        )
        chunks = self._chunk_content(draft.content, draft.chunk_size)
        for index, content in enumerate(chunks):
            chunk_id = self._stable_id("knowledge_chunk", document_id, str(index), hashlib.sha256(content.encode("utf-8")).hexdigest())
            await self.db.execute(
                """INSERT INTO knowledge_chunks
                       (id, tenant_id, collection_id, document_id, chunk_index, content,
                        keywords, metadata, artifact_id, artifact_version_id, created_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)""",
                (
                    chunk_id,
                    context.actor.tenant_id,
                    collection_id,
                    document_id,
                    index,
                    content,
                    canonical_json(self._terms(content)),
                    canonical_json(draft.metadata),
                    draft.artifact_id,
                    draft.artifact_version_id,
                    created_at,
                ),
            )
            if draft.artifact_id and draft.artifact_version_id:
                await self._link_artifact_document(draft.artifact_id, draft.artifact_version_id, document_id, chunk_id)

    async def _candidate_chunks(self, collections: list[str]) -> list:
        context = get_execution_context()
        if collections:
            placeholders = self._placeholders(2, len(collections))
            return await self.db.fetch_all(
                f"""SELECT ch.id AS chunk_id, ch.document_id, ch.content, ch.metadata AS chunk_metadata,
                               ch.artifact_id, ch.artifact_version_id, d.title, d.source_type,
                               d.source_uri, d.acl_tags, c.name AS collection_name
                        FROM knowledge_chunks ch
                        JOIN knowledge_documents d ON d.id = ch.document_id
                        JOIN knowledge_collections c ON c.id = ch.collection_id
                        WHERE ch.tenant_id = $1 AND d.status = 'active' AND c.name IN ({placeholders})""",
                (context.actor.tenant_id, *collections),
            )
        return await self.db.fetch_all(
            """SELECT ch.id AS chunk_id, ch.document_id, ch.content, ch.metadata AS chunk_metadata,
                          ch.artifact_id, ch.artifact_version_id, d.title, d.source_type,
                          d.source_uri, d.acl_tags, c.name AS collection_name
                   FROM knowledge_chunks ch
                   JOIN knowledge_documents d ON d.id = ch.document_id
                   JOIN knowledge_collections c ON c.id = ch.collection_id
                   WHERE ch.tenant_id = $1 AND d.status = 'active'""",
            (context.actor.tenant_id,),
        )

    async def _link_artifact_document(
        self,
        artifact_id: str,
        artifact_version_id: str,
        document_id: str,
        chunk_id: str | None,
    ) -> None:
        await self.db.execute(
            """INSERT INTO artifact_knowledge_links
                   (id, artifact_id, artifact_version_id, document_id, chunk_id, relationship, created_at)
                   VALUES ($1, $2, $3, $4, $5, 'indexes', $6)
                   ON CONFLICT (artifact_version_id, document_id, chunk_id, relationship) DO NOTHING""",
            (new_id(), artifact_id, artifact_version_id, document_id, chunk_id, self._now()),
        )

    async def _record_retrieval_audit(
        self,
        *,
        request: KnowledgeSearchRequest,
        collection_names: list[str],
        result_count: int,
        filtered_count: int,
        source_refs: list[dict[str, Any]],
    ) -> None:
        context = get_execution_context()
        await self.db.execute(
            """INSERT INTO retrieval_audit_events
                   (id, tenant_id, trace_id, agent_id, query, collection_names,
                    result_count, filtered_count, source_refs, created_by, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)""",
            (
                new_id(),
                context.actor.tenant_id,
                context.trace_id,
                request.agent_id,
                request.query,
                canonical_json(collection_names),
                result_count,
                filtered_count,
                canonical_json(source_refs),
                context.actor.actor_id,
                self._now(),
            ),
        )
        await self.db.commit()

    @staticmethod
    def _chunk_content(content: str, chunk_size: int) -> list[str]:
        normalized = content.strip()
        if not normalized:
            return []
        size = min(max(chunk_size, 400), 4000)
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs:
            if current and len(current) + len(paragraph) + 2 > size:
                chunks.append(current)
                current = paragraph
            else:
                current = paragraph if not current else f"{current}\n\n{paragraph}"
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _terms(text: str) -> list[str]:
        return sorted(set(re.findall(r"[\w\u4e00-\u9fff]{2,}", text.lower())))

    def _score(self, query_terms: list[str], content: str, title: str, strategy: RetrievalStrategy) -> float:
        if not query_terms:
            return 0
        haystack = f"{title}\n{content}".lower()
        matches = sum(1 for term in query_terms if term in haystack)
        if matches == 0:
            return 0
        base = matches / len(query_terms)
        title_boost = 0.2 if any(term in title.lower() for term in query_terms) else 0
        strategy_boost = 0.1 if strategy == RetrievalStrategy.HYBRID else 0
        return min(base + title_boost + strategy_boost, 1.0)

    @staticmethod
    def _stable_id(*parts: str) -> str:
        return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _loads(value: str | None, default):
        if not value:
            return default
        return json.loads(value)

    def _json_row(self, row, *fields: str) -> dict:
        result = dict(row)
        for field in fields:
            result[field] = self._loads(result.get(field), {})
        return result

    def _document_row(self, row) -> dict:
        result = dict(row)
        result["metadata"] = self._loads(result.get("metadata"), {})
        result["acl_tags"] = self._loads(result.get("acl_tags"), [])
        result["chunk_count"] = int(result.get("chunk_count") or 0)
        result["raw_status"] = result.get("status") or "active"
        result["status"] = "indexed" if result["raw_status"] == "active" else result["raw_status"]
        return result

    def _placeholders(self, start: int, count: int) -> str:
        return ",".join(f"${index}" for index in range(start, start + count))
        return ",".join("?" for _ in range(count))

    def _now(self):
        return datetime.now(timezone.utc).replace(tzinfo=None)
