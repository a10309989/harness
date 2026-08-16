"""Transactional immutable artifact catalog backed by content-addressed storage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from harness.artifacts.storage import StorageBackend
from harness.db.protocols import DatabaseProtocol
from harness.models.artifact import ArtifactDraft, ArtifactVersionRef
from harness.observability.audit import AuditService
from harness.observability.context import get_execution_context, new_id
from harness.observability.redaction import canonical_json

class ArtifactService:
    def __init__(
        self,
        db: DatabaseProtocol,
        storage: StorageBackend,
        audit_service: AuditService | None = None,
    ) -> None:
        self.db = db
        self.storage = storage
        self.audit_service = audit_service

    async def create(
        self,
        draft: ArtifactDraft,
        *,
        connection=None,
    ) -> ArtifactVersionRef:
        context = get_execution_context()
        artifact_id = draft.artifact_id or new_id()
        version_id = new_id()
        content = self._content_bytes(draft.content)
        digest, storage_key = await self.storage.put(content)
        now = self._now()

        async def write(executor) -> None:
            await executor.execute(
                """INSERT INTO artifacts
                       (id, tenant_id, artifact_type, name, status,
                        current_version_id, created_by, created_at)
                       VALUES ($1, $2, $3, $4, 'active', $5, $6, $7)""",
                (
                    artifact_id,
                    context.actor.tenant_id,
                    str(draft.artifact_type),
                    draft.name,
                    version_id,
                    context.actor.actor_id,
                    now,
                ),
            )
            await self._insert_version(
                executor,
                artifact_id=artifact_id,
                version_id=version_id,
                version_number=1,
                digest=digest,
                storage_key=storage_key,
                media_type=draft.media_type,
                size_bytes=len(content),
                metadata=draft.metadata,
                created_by=context.actor.actor_id,
                trace_id=context.trace_id,
                created_at=now,
            )
            if self.audit_service:
                await self.audit_service.record(
                    "artifact.created",
                    resource_type="artifact",
                    resource_id=artifact_id,
                    output_data={"version_id": version_id, "digest": digest},
                    metadata={
                        "artifact_type": str(draft.artifact_type),
                        "version_number": 1,
                    },
                    connection=executor,
                )
        if connection is None:
            async with self.db.transaction() as transaction:
                await write(transaction)
        else:
            await write(connection)
        return ArtifactVersionRef(
            artifact_id=artifact_id,
            version_id=version_id,
            version_number=1,
            content_digest=digest,
            media_type=draft.media_type,
            size_bytes=len(content),
        )

    async def create_version(
        self,
        artifact_id: str,
        content: str | bytes,
        *,
        media_type: str = "text/plain",
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactVersionRef:
        context = get_execution_context()
        raw = self._content_bytes(content)
        digest, storage_key = await self.storage.put(raw)
        version_id = new_id()
        now = self._now()

        async with self.db.transaction() as connection:
            row = await (
                await connection.execute(
                    """SELECT COALESCE(MAX(v.version_number), 0) AS latest, a.status
                           FROM artifacts a
                           LEFT JOIN artifact_versions v ON v.artifact_id = a.id
                           WHERE a.id = $1 AND a.tenant_id = $2
                           GROUP BY a.id""",
                    (artifact_id, context.actor.tenant_id),
                )
            ).fetchone()
            if row is None:
                raise KeyError(f"Artifact '{artifact_id}' not found")
            if row["status"] != "active":
                raise ValueError("Archived artifacts cannot receive new versions")
            version_number = row["latest"] + 1
            await self._insert_version(
                connection,
                artifact_id=artifact_id,
                version_id=version_id,
                version_number=version_number,
                digest=digest,
                storage_key=storage_key,
                media_type=media_type,
                size_bytes=len(raw),
                metadata=metadata or {},
                created_by=context.actor.actor_id,
                trace_id=context.trace_id,
                created_at=now,
            )
            await connection.execute(
                "UPDATE artifacts SET current_version_id = $1 WHERE id = $2",
                (version_id, artifact_id),
            )
            if self.audit_service:
                await self.audit_service.record(
                    "artifact.version_created",
                    resource_type="artifact",
                    resource_id=artifact_id,
                    output_data={"version_id": version_id, "digest": digest},
                    metadata={"version_number": version_number},
                    connection=connection,
                )
        return ArtifactVersionRef(
            artifact_id=artifact_id,
            version_id=version_id,
            version_number=version_number,
            content_digest=digest,
            media_type=media_type,
            size_bytes=len(raw),
        )

    async def link(
        self,
        source_version_id: str,
        target_version_id: str,
        relationship: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        context = get_execution_context()
        link_id = new_id()
        now = self._now()
        async with self.db.transaction() as connection:
            for version_id in (source_version_id, target_version_id):
                exists = await (
                    await connection.execute(
                        """SELECT v.id FROM artifact_versions v
                               JOIN artifacts a ON a.id = v.artifact_id
                               WHERE v.id = $1 AND a.tenant_id = $2""",
                        (version_id, context.actor.tenant_id),
                    )
                ).fetchone()
                if not exists:
                    raise KeyError(f"Artifact version '{version_id}' not found")
            await connection.execute(
                """INSERT INTO artifact_links
                       (id, source_version_id, target_version_id, relationship,
                        metadata, created_by, created_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                (
                    link_id,
                    source_version_id,
                    target_version_id,
                    relationship,
                    canonical_json(metadata or {}),
                    context.actor.actor_id,
                    now,
                ),
            )
            if self.audit_service:
                await self.audit_service.record(
                    "artifact.link_created",
                    resource_type="artifact_link",
                    resource_id=link_id,
                    metadata={
                        "source_version_id": source_version_id,
                        "target_version_id": target_version_id,
                        "relationship": relationship,
                    },
                    connection=connection,
                )
        return {
            "id": link_id,
            "source_version_id": source_version_id,
            "target_version_id": target_version_id,
            "relationship": relationship,
        }

    async def archive(self, artifact_id: str) -> bool:
        context = get_execution_context()
        now = self._now()
        async with self.db.transaction() as connection:
            cursor = await connection.execute(
                """UPDATE artifacts SET status = 'archived', archived_at = $1
                       WHERE id = $2 AND tenant_id = $3 AND status = 'active'""",
                (now, artifact_id, context.actor.tenant_id),
            )
            if cursor.rowcount == 0:
                return False
            if self.audit_service:
                await self.audit_service.record(
                    "artifact.archived",
                    resource_type="artifact",
                    resource_id=artifact_id,
                    connection=connection,
                )
        return True

    async def get(self, artifact_id: str) -> dict | None:
        context = get_execution_context()
        artifact = await self.db.fetch_one(
            "SELECT * FROM artifacts WHERE id = $1 AND tenant_id = $2",
            (artifact_id, context.actor.tenant_id),
        )
        if artifact is None:
            return None
        versions = await self.db.fetch_all(
            """SELECT * FROM artifact_versions WHERE artifact_id = $1
                   ORDER BY version_number DESC""",
            (artifact_id,),
        )
        result = dict(artifact)
        result["versions"] = [self._version_dict(row) for row in versions]
        return result

    async def list(
        self,
        *,
        artifact_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        context = get_execution_context()
        clauses = ["tenant_id = $1"]
        params: list[Any] = [context.actor.tenant_id]
        if artifact_type:
            clauses.append(f"artifact_type = ${len(params) + 1}")
            params.append(artifact_type)
        if status:
            clauses.append(f"status = ${len(params) + 1}")
            params.append(status)
        params.extend([min(max(limit, 1), 500), max(offset, 0)])
        rows = await self.db.fetch_all(
            f"""SELECT * FROM artifacts WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC LIMIT ${len(params) - 1} OFFSET ${len(params)}""",
            tuple(params),
        )
        return [dict(row) for row in rows]

    async def read_version(self, version_id: str) -> tuple[dict, bytes]:
        context = get_execution_context()
        row = await self.db.fetch_one(
            """SELECT v.* FROM artifact_versions v
                   JOIN artifacts a ON a.id = v.artifact_id
                   WHERE v.id = $1 AND a.tenant_id = $2""",
            (version_id, context.actor.tenant_id),
        )
        if row is None:
            raise KeyError(f"Artifact version '{version_id}' not found")
        content = await self.storage.get(row["storage_key"], row["content_digest"])
        return self._version_dict(row), content

    async def get_lineage(self, version_id: str) -> dict:
        context = get_execution_context()
        visible = await self.db.fetch_one(
            """SELECT v.id FROM artifact_versions v
                   JOIN artifacts a ON a.id = v.artifact_id
                   WHERE v.id = $1 AND a.tenant_id = $2""",
            (version_id, context.actor.tenant_id),
        )
        if visible is None:
            raise KeyError(f"Artifact version '{version_id}' not found")
        incoming = await self.db.fetch_all(
            """SELECT l.* FROM artifact_links l
                   JOIN artifact_versions v ON v.id = l.source_version_id
                   JOIN artifacts a ON a.id = v.artifact_id
                   WHERE l.target_version_id = $1 AND a.tenant_id = $2""",
            (version_id, context.actor.tenant_id),
        )
        outgoing = await self.db.fetch_all(
            """SELECT l.* FROM artifact_links l
                   JOIN artifact_versions v ON v.id = l.target_version_id
                   JOIN artifacts a ON a.id = v.artifact_id
                   WHERE l.source_version_id = $1 AND a.tenant_id = $2""",
            (version_id, context.actor.tenant_id),
        )
        return {
            "version_id": version_id,
            "incoming": [self._link_dict(row) for row in incoming],
            "outgoing": [self._link_dict(row) for row in outgoing],
        }

    @staticmethod
    def _content_bytes(content: str | bytes) -> bytes:
        return content if isinstance(content, bytes) else content.encode("utf-8")

    async def _insert_version(self, connection, **values) -> None:
        await connection.execute(
            """INSERT INTO artifact_versions
                   (id, artifact_id, version_number, content_digest, storage_key,
                    media_type, size_bytes, metadata, created_by, trace_id, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)""",
            (
                values["version_id"],
                values["artifact_id"],
                values["version_number"],
                values["digest"],
                values["storage_key"],
                values["media_type"],
                values["size_bytes"],
                canonical_json(values["metadata"]),
                values["created_by"],
                values["trace_id"],
                values["created_at"],
            ),
        )

    @staticmethod
    def _version_dict(row) -> dict:
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"]) if result["metadata"] else {}
        return result

    @staticmethod
    def _link_dict(row) -> dict:
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"]) if result["metadata"] else {}
        return result

    def _now(self):
        return datetime.now(timezone.utc).replace(tzinfo=None)
