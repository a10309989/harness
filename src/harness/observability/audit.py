"""Durable, append-only audit events with tamper-evident hash chaining."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from harness.db.protocols import DatabaseProtocol
from harness.observability.context import get_execution_context, new_id
from harness.observability.redaction import canonical_json, content_digest, redact

_default_audit_service: "AuditService | None" = None


def set_default_audit_service(service: "AuditService | None") -> None:
    global _default_audit_service
    _default_audit_service = service


async def record_audit(event_type: str, **kwargs) -> str | None:
    if _default_audit_service is None:
        return None
    return await _default_audit_service.record(event_type, **kwargs)


class AuditService:
    def __init__(self, db: DatabaseProtocol) -> None:
        self.db = db

    async def record(
        self,
        event_type: str,
        *,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        decision: str | None = None,
        reason: str | None = None,
        input_data: Any = None,
        output_data: Any = None,
        metadata: dict | None = None,
        connection=None,
    ) -> str:
        context = get_execution_context()
        event_id = new_id()
        payload = {
            "id": event_id,
            "tenant_id": context.actor.tenant_id,
            "trace_id": context.trace_id,
            "span_id": context.span_id,
            "parent_span_id": context.parent_span_id,
            "workflow_id": context.workflow_id,
            "session_id": context.session_id,
            "actor_id": context.actor.actor_id,
            "actor_type": context.actor.actor_type.value,
            "event_type": event_type,
            "action": action or event_type,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "decision": decision,
            "reason": reason,
            "input_digest": content_digest(input_data) if input_data is not None else None,
            "output_digest": content_digest(output_data) if output_data is not None else None,
            "metadata": redact(metadata or {}),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await self.enqueue("audit", payload, connection=connection)
        return event_id

    async def enqueue(self, topic: str, payload: dict, connection=None) -> str:
        event_id = payload.get("id") or new_id()
        now = datetime.now(timezone.utc).isoformat()
        executor = connection or self.db
        await executor.execute(
    """INSERT INTO outbox_events
       (id, topic, payload, status, attempts, available_at, created_at)
       VALUES ($1, $2, $3, 'pending', 0, $4, $5)""",
    (
        event_id,
        topic,
        canonical_json(payload),
        self._postgres_timestamp(now),
        self._postgres_timestamp(now),
    ),
)
        if connection is None:
            await self.db.commit()
        return event_id

    async def persist_audit_payload(self, payload: dict) -> bool:
        tenant_id = payload.get("tenant_id", "default")
        async with self.db.transaction() as connection:
            return await self._persist_audit_payload_postgres(
                connection, payload, tenant_id
            )

    async def _persist_audit_payload_postgres(
        self, connection, payload: dict, tenant_id: str
    ) -> bool:
        existing = await (
            await connection.execute(
                "SELECT id FROM audit_events WHERE id = $1",
                (payload["id"],),
            )
        ).fetchone()
        if existing:
            return False

        head = await (
            await connection.execute(
                """SELECT event_hash FROM audit_chain_heads
                   WHERE tenant_id = $1""",
                (tenant_id,),
            )
        ).fetchone()
        if head is None:
            head = await (
                await connection.execute(
                    """SELECT event_hash FROM audit_events
                       WHERE tenant_id = $1
                       ORDER BY created_at DESC, id DESC LIMIT 1""",
                    (tenant_id,),
                )
            ).fetchone()
        previous_hash = head["event_hash"] if head else ""
        event_hash = self._calculate_hash(previous_hash, payload)
        created_at = self._postgres_timestamp(payload["created_at"])

        await connection.execute(
            """INSERT INTO audit_events (
                id, tenant_id, trace_id, span_id, parent_span_id,
                workflow_id, session_id, actor_id, actor_type,
                event_type, action, resource_type, resource_id,
                decision, reason, input_digest, output_digest,
                metadata, previous_hash, event_hash, created_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                $12, $13, $14, $15, $16, $17, $18, $19, $20, $21
            )""",
            (
                payload["id"],
                tenant_id,
                payload["trace_id"],
                payload["span_id"],
                payload.get("parent_span_id"),
                payload.get("workflow_id"),
                payload.get("session_id"),
                payload["actor_id"],
                payload["actor_type"],
                payload["event_type"],
                payload["action"],
                payload.get("resource_type"),
                payload.get("resource_id"),
                payload.get("decision"),
                payload.get("reason"),
                payload.get("input_digest"),
                payload.get("output_digest"),
                canonical_json(payload.get("metadata", {})),
                previous_hash or None,
                event_hash,
                created_at,
            ),
        )
        await connection.execute(
            """INSERT INTO audit_chain_heads
               (tenant_id, event_hash, event_id, updated_at)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT(tenant_id) DO UPDATE SET
                 event_hash = excluded.event_hash,
                 event_id = excluded.event_id,
                 updated_at = excluded.updated_at""",
            (
                tenant_id,
                event_hash,
                payload["id"],
                created_at,
            ),
        )
        return True

    async def list_events(
        self,
        *,
        trace_id: str | None = None,
        session_id: str | None = None,
        actor_id: str | None = None,
        event_type: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        decision: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        context = get_execution_context()
        clauses = ["tenant_id = $1"]
        params: list[Any] = [context.actor.tenant_id]
        filters = {
            "trace_id": trace_id,
            "session_id": session_id,
            "actor_id": actor_id,
            "event_type": event_type,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "decision": decision,
        }
        for column, value in filters.items():
            if value is not None:
                clauses.append(f"{column} = ${len(params) + 1}")
                params.append(value)
        params.extend([min(max(limit, 1), 500), max(offset, 0)])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self.db.fetch_all(
            f"""SELECT * FROM audit_events {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ${len(params) - 1} OFFSET ${len(params)}""",
            tuple(params),
        )
        return [self._row_to_dict(row) for row in rows]

    async def get_event(self, event_id: str) -> dict | None:
        context = get_execution_context()
        row = await self.db.fetch_one(
            "SELECT * FROM audit_events WHERE id = $1 AND tenant_id = $2",
            (event_id, context.actor.tenant_id),
        )
        return self._row_to_dict(row) if row else None

    async def verify_chain(self, tenant_id: str | None = None) -> dict:
        tenant_id = tenant_id or get_execution_context().actor.tenant_id
        rows = await self.db.fetch_all(
            """SELECT * FROM audit_events
               WHERE tenant_id = $1 ORDER BY created_at, id""",
            (tenant_id,),
        )
        previous_hash = ""
        for index, row in enumerate(rows):
            payload = self._payload_from_row(row)
            expected = self._calculate_hash(previous_hash, payload)
            if (row["previous_hash"] or "") != previous_hash or row["event_hash"] != expected:
                return {
                    "valid": False,
                    "tenant_id": tenant_id,
                    "checked": index,
                    "failed_event_id": row["id"],
                }
            previous_hash = row["event_hash"]
        return {
            "valid": True,
            "tenant_id": tenant_id,
            "checked": len(rows),
            "head_hash": previous_hash or None,
        }

    @staticmethod
    def _calculate_hash(previous_hash: str, payload: dict) -> str:
        material = f"{previous_hash}|{canonical_json(payload)}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _row_to_dict(row) -> dict:
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"]) if result.get("metadata") else {}
        result["created_at"] = AuditService._iso_timestamp(result["created_at"])
        return result

    @staticmethod
    def _payload_from_row(row) -> dict:
        return {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "trace_id": row["trace_id"],
            "span_id": row["span_id"],
            "parent_span_id": row["parent_span_id"],
            "workflow_id": row["workflow_id"],
            "session_id": row["session_id"],
            "actor_id": row["actor_id"],
            "actor_type": row["actor_type"],
            "event_type": row["event_type"],
            "action": row["action"],
            "resource_type": row["resource_type"],
            "resource_id": row["resource_id"],
            "decision": row["decision"],
            "reason": row["reason"],
            "input_digest": row["input_digest"],
            "output_digest": row["output_digest"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
            "created_at": AuditService._iso_timestamp(row["created_at"]),
        }

    @staticmethod
    def _postgres_timestamp(value: str | datetime) -> datetime:
        if isinstance(value, datetime):
            source = value
        else:
            source = datetime.fromisoformat(value)
        if source.tzinfo is None:
            return source
        return source.astimezone(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _iso_timestamp(value: str | datetime) -> str:
        if isinstance(value, datetime):
            source = value
            if source.tzinfo is None:
                source = source.replace(tzinfo=timezone.utc)
            else:
                source = source.astimezone(timezone.utc)
            return source.isoformat()
        return value
