"""Agent execution timeline events backed by audit storage."""

from __future__ import annotations

from typing import Any

from harness.observability.audit import AuditService, record_audit


class AgentEventService:
    """Records and lists session-scoped agent timeline events."""

    def __init__(self, audit_service: AuditService) -> None:
        self.audit_service = audit_service

    async def record(
        self,
        event_type: str,
        *,
        session_id: str,
        agent_id: str,
        step: str,
        status: str = "completed",
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        return await record_audit(
            event_type,
            resource_type="agent",
            resource_id=agent_id,
            decision=status,
            metadata={
                "kind": "agent_timeline",
                "session_id": session_id,
                "agent_id": agent_id,
                "step": step,
                "status": status,
                "message": message,
                **(metadata or {}),
            },
        )

    async def list_by_session(self, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = await self.audit_service.list_events(
            session_id=session_id,
            limit=limit,
        )
        events = []
        for row in rows:
            metadata = row.get("metadata") or {}
            if metadata.get("kind") != "agent_timeline":
                continue
            events.append(
                {
                    "event_id": row["id"],
                    "event_type": row["event_type"],
                    "session_id": session_id,
                    "agent_id": metadata.get("agent_id") or row.get("resource_id"),
                    "step": metadata.get("step", ""),
                    "status": metadata.get("status") or row.get("decision") or "completed",
                    "message": metadata.get("message", ""),
                    "metadata": {
                        key: value
                        for key, value in metadata.items()
                        if key not in {"kind", "session_id", "agent_id", "step", "status", "message"}
                    },
                    "created_at": row["created_at"],
                }
            )
        return list(reversed(events))
