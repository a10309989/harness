"""Human task state machine and one-time approval grants."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from harness.artifacts.service import ArtifactService
from harness.db.protocols import DatabaseProtocol
from harness.models.workflow import (
    ApprovalGrantState,
    HumanTaskState,
    WorkflowState,
)
from harness.observability.audit import AuditService
from harness.observability.context import get_execution_context, new_id
from harness.observability.redaction import canonical_json, content_digest
from harness.workflow.service import WorkflowService

class HumanTaskService:
    """Persists claims and immutable approval actions with separation of duties."""

    ACTIVE_STATES = {
        HumanTaskState.PENDING,
        HumanTaskState.CLAIMED,
        HumanTaskState.PARTIALLY_APPROVED,
    }

    def __init__(
        self,
        db: DatabaseProtocol,
        workflow_service: WorkflowService,
        artifact_service: ArtifactService,
        audit_service: AuditService,
        dev_mode: bool = False,
    ) -> None:
        self.db = db
        self.workflow_service = workflow_service
        self.artifact_service = artifact_service
        self.audit_service = audit_service
        # In dev mode every request maps to the same implicit admin, so the
        # requester IS the only actor available. Relax separation-of-duties for
        # local testing; production (non-dev) still enforces it.
        self._dev_mode = dev_mode

    async def create_task(
        self,
        *,
        workflow_id: str,
        title: str,
        description: str,
        checkpoint_id: str,
        metadata: dict[str, Any] | None = None,
        required_approvals: int = 1,
    ) -> str:
        """Create a human approval task for a pipeline artifact.

        Unlike tool-call approval (``suspend_for_tool``), these tasks gate a
        produced artifact (test strategy / test cases / scripts) before the next
        pipeline stage runs. The requester is the current actor; in dev mode the
        requester may also approve (see ``_dev_mode``).
        """
        context = get_execution_context()
        task_id = new_id()
        now = self._now()
        async with self.db.transaction() as connection:
            await connection.execute(
                """INSERT INTO human_tasks
                   (id, tenant_id, workflow_id, checkpoint_id, title, description,
                    state, required_approvals, requester_id, metadata, created_at, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6, 'pending', $7, $8, $9, $10, $11)""",
                (
                    task_id,
                    context.actor.tenant_id,
                    workflow_id,
                    checkpoint_id,
                    title,
                    description,
                    required_approvals,
                    context.actor.actor_id,
                    canonical_json(metadata or {}),
                    now,
                    now,
                ),
            )
        await self.audit_service.record(
            "human_task.created",
            resource_type="human_task",
            resource_id=task_id,
            metadata={"workflow_id": workflow_id, "title": title, **(metadata or {})},
        )
        return task_id

    async def claim(
        self,
        task_id: str,
        *,
        idempotency_key: str,
        lease_minutes: int = 15,
    ) -> dict:
        context = get_execution_context()
        now = self._now()
        expires_at_source = (
            datetime.now(timezone.utc) + timedelta(minutes=max(1, lease_minutes))
        )
        expires_at = self._datetime_to_db(expires_at_source)
        expires_at_event = self._event_timestamp(expires_at_source)
        async with self.db.transaction() as connection:
            existing = await self._idempotent_action(
                connection,
                task_id,
                idempotency_key,
            )
            if existing:
                return await self.get(task_id, connection=connection)
            task = await self._task_row(connection, task_id)
            if task is None:
                raise KeyError(f"Human task '{task_id}' not found")
            state = HumanTaskState(task["state"])
            claim_expired = (
                task["claim_expires_at"] is not None
                and self._is_expired(task["claim_expires_at"], now)
            )
            if state == HumanTaskState.CLAIMED and not claim_expired:
                if task["assignee_id"] == context.actor.actor_id:
                    return self._task_dict(task)
                raise ValueError("Human task is already claimed")
            if state not in {
                HumanTaskState.PENDING,
                HumanTaskState.PARTIALLY_APPROVED,
                HumanTaskState.CLAIMED,
            }:
                raise ValueError(f"Human task cannot be claimed from '{state}'")
            await connection.execute(
                """UPDATE human_tasks
                       SET state = 'claimed', assignee_id = $1, claim_expires_at = $2,
                           version = version + 1, updated_at = $3
                       WHERE id = $4""",
                (context.actor.actor_id, expires_at, now, task_id),
            )
            await self._append_action(
                connection,
                task_id,
                "claim",
                idempotency_key=idempotency_key,
                metadata={"claim_expires_at": expires_at_event},
            )
            await self.audit_service.record(
                "human_task.claimed",
                resource_type="human_task",
                resource_id=task_id,
                metadata={"claim_expires_at": expires_at_event},
                connection=connection,
            )
        return await self.get(task_id)

    async def release(
        self,
        task_id: str,
        *,
        idempotency_key: str,
    ) -> dict:
        context = get_execution_context()
        async with self.db.transaction() as connection:
            existing = await self._idempotent_action(
                connection,
                task_id,
                idempotency_key,
            )
            if existing:
                return await self.get(task_id, connection=connection)
            task = await self._task_row(connection, task_id)
            if task is None:
                raise KeyError(f"Human task '{task_id}' not found")
            if task["state"] != str(HumanTaskState.CLAIMED):
                raise ValueError("Only claimed tasks can be released")
            if task["assignee_id"] != context.actor.actor_id:
                raise PermissionError("Only the current assignee can release the task")
            approval_count = await self._approval_count(connection, task_id)
            target = (
                HumanTaskState.PARTIALLY_APPROVED
                if approval_count > 0
                else HumanTaskState.PENDING
            )
            await connection.execute(
                """UPDATE human_tasks
                       SET state = $1, assignee_id = NULL, claim_expires_at = NULL,
                           version = version + 1, updated_at = $2
                       WHERE id = $3""",
                (str(target), self._now(), task_id),
            )
            await self._append_action(
                connection,
                task_id,
                "release",
                idempotency_key=idempotency_key,
            )
            await self.audit_service.record(
                "human_task.released",
                resource_type="human_task",
                resource_id=task_id,
                connection=connection,
            )
        return await self.get(task_id)

    async def approve(
        self,
        task_id: str,
        *,
        comment: str = "",
        idempotency_key: str,
        grant_ttl_minutes: int = 30,
    ) -> dict:
        context = get_execution_context()
        task_snapshot = await self.get(task_id)
        if task_snapshot is None:
            raise KeyError(f"Human task '{task_id}' not found")
        checkpoint_row, checkpoint = await self.workflow_service.load_checkpoint_by_id(
            task_snapshot["checkpoint_id"]
        )
        # Pipeline-artifact checkpoints carry an artifact digest instead of tool
        # params; tool-call checkpoints carry ``params``.
        params_digest = content_digest(
            checkpoint["params"]
            if checkpoint.get("kind") == "tool_call"
            else checkpoint.get("artifact_digest", "pipeline")
        )
        now = self._now()

        async with self.db.transaction() as connection:
            existing = await self._idempotent_action(
                connection,
                task_id,
                idempotency_key,
            )
            if existing:
                return await self.get(task_id, connection=connection)
            task = await self._task_row(connection, task_id)
            if task is None:
                raise KeyError(f"Human task '{task_id}' not found")
            state = HumanTaskState(task["state"])
            if state not in self.ACTIVE_STATES:
                raise ValueError(f"Human task cannot be approved from '{state}'")
            if (not self._dev_mode and task["requester_id"] == context.actor.actor_id):
                raise PermissionError("Requesters cannot approve their own task")
            if (
                state == HumanTaskState.CLAIMED
                and task["assignee_id"] != context.actor.actor_id
                and not self._claim_expired(task, now)
            ):
                raise PermissionError("Only the current assignee can approve the task")
            duplicate = await (
                await connection.execute(
                    """SELECT id FROM human_task_actions
                           WHERE task_id = $1 AND actor_id = $2 AND action = 'approve'""",
                    (task_id, context.actor.actor_id),
                )
            ).fetchone()
            if duplicate:
                raise ValueError("The same actor cannot approve a task twice")
            await self._append_action(
                connection,
                task_id,
                "approve",
                comment=comment,
                idempotency_key=idempotency_key,
            )
            approvals = await self._approval_count(connection, task_id)
            completed = approvals >= task["required_approvals"]
            target = (
                HumanTaskState.APPROVED
                if completed
                else HumanTaskState.PARTIALLY_APPROVED
            )
            await connection.execute(
    """UPDATE human_tasks
       SET state = $1, assignee_id = NULL, claim_expires_at = NULL,
           version = version + 1, updated_at = $2,
           completed_at = $3
       WHERE id = $4""",
    (str(target), now, now if completed else None, task_id),
)
            grant_id = None
            if completed:
                grant_id = new_id()
                # Pipeline-artifact approvals carry their own checkpoint kind and
                # reference the produced artifact; tool-call approvals reference
                # the gated tool. Both resume the workflow on completion.
                is_tool = checkpoint.get("kind") == "tool_call"
                resource_type = "tool" if is_tool else "artifact"
                resource_id = (
                    checkpoint.get("tool_name")
                    if is_tool
                    else checkpoint.get("artifact_version_id")
                )
                input_digest = (
                    params_digest
                    if is_tool
                    else checkpoint.get("artifact_digest") or params_digest
                )
                expires_at_source = (
                    datetime.now(timezone.utc)
                    + timedelta(minutes=max(1, grant_ttl_minutes))
                )
                expires_at = self._datetime_to_db(expires_at_source)
                await connection.execute(
                    """INSERT INTO approval_grants
                           (id, task_id, workflow_id, resource_type, resource_id,
                            input_digest, state, created_by, expires_at, created_at)
                           VALUES ($1, $2, $3, $4, $5, $6, 'active', $7, $8, $9)""",
                    (
                        grant_id,
                        task_id,
                        task["workflow_id"],
                        resource_type,
                        resource_id,
                        input_digest,
                        context.actor.actor_id,
                        expires_at,
                        now,
                    ),
                )
                if is_tool:
                    # Tool-call approvals are resumed by the resume worker.
                    try:
                        await self.workflow_service.request_resume(
                            task["workflow_id"],
                            reason="human approval completed",
                            connection=connection,
                        )
                    except ValueError as resume_err:
                        # The workflow may already be in a terminal state (e.g.
                        # a concurrent resume raced). The approval still succeeded.
                        await self.audit_service.record(
                            "workflow.resume_skipped",
                            resource_type="workflow",
                            resource_id=task["workflow_id"],
                            decision="skipped",
                            reason=f"Approval completed but workflow cannot resume: {resume_err}",
                            connection=connection,
                        )
                # Pipeline-artifact approvals are advanced by the caller (the
                # frontend calls resume_pipeline after approve) — do NOT also
                # request_resume here, or the pipeline would double-advance and
                # gate duplicate nodes.
            await self.audit_service.record(
                "human_task.approved",
                resource_type="human_task",
                resource_id=task_id,
                decision=str(target),
                reason=comment or None,
                metadata={
                    "approval_count": approvals,
                    "required_approvals": task["required_approvals"],
                    "approval_grant_id": grant_id,
                    "checkpoint_id": checkpoint_row["id"],
                },
                connection=connection,
            )
        return await self.get(task_id)

    async def reject(
        self,
        task_id: str,
        *,
        comment: str,
        idempotency_key: str,
    ) -> dict:
        context = get_execution_context()
        now = self._now()
        async with self.db.transaction() as connection:
            existing = await self._idempotent_action(
                connection,
                task_id,
                idempotency_key,
            )
            if existing:
                return await self.get(task_id, connection=connection)
            task = await self._task_row(connection, task_id)
            if task is None:
                raise KeyError(f"Human task '{task_id}' not found")
            state = HumanTaskState(task["state"])
            if state not in self.ACTIVE_STATES:
                raise ValueError(f"Human task cannot be rejected from '{state}'")
            if (not self._dev_mode and task["requester_id"] == context.actor.actor_id):
                raise PermissionError("Requesters cannot reject their own task")
            if (
                state == HumanTaskState.CLAIMED
                and task["assignee_id"] != context.actor.actor_id
                and not self._claim_expired(task, now)
            ):
                raise PermissionError("Only the current assignee can reject the task")
            await connection.execute(
                """UPDATE human_tasks
                       SET state = 'rejected', assignee_id = NULL,
                           claim_expires_at = NULL, version = version + 1,
                           updated_at = $1, completed_at = $2
                       WHERE id = $3""",
                (now, now, task_id),
            )
            await self._append_action(
                connection,
                task_id,
                "reject",
                comment=comment,
                idempotency_key=idempotency_key,
            )
            workflow = await self.workflow_service._workflow_row(
                connection,
                task["workflow_id"],
            )
            await self.workflow_service._transition(
                connection,
                task["workflow_id"],
                WorkflowState(workflow["state"]),
                WorkflowState.CANCELLED,
                "human task rejected",
                metadata={"human_task_id": task_id},
            )
            await self.audit_service.record(
                "human_task.rejected",
                resource_type="human_task",
                resource_id=task_id,
                decision="rejected",
                reason=comment,
                connection=connection,
            )
        return await self.get(task_id)

    async def validate_and_consume_grant(
        self,
        grant_id: str,
        *,
        resource_id: str,
        params: dict[str, Any],
    ) -> dict:
        now = self._now()
        digest = content_digest(params)
        context = get_execution_context()
        existing = await self.db.fetch_one(
            """SELECT g.* FROM approval_grants g
                   JOIN human_tasks t ON t.id = g.task_id
                   WHERE g.id = $1 AND t.tenant_id = $2""",
            (grant_id, context.actor.tenant_id),
        )
        if existing is None:
            raise KeyError(f"Approval grant '{grant_id}' not found")
        if self._is_expired(existing["expires_at"], now) and existing["state"] == str(
            ApprovalGrantState.ACTIVE
        ):
            await self.db.execute(
                """UPDATE approval_grants SET state = 'expired'
                       WHERE id = $1 AND state = 'active'""",
                (grant_id,),
            )
            await self.db.commit()
            raise ValueError("Approval grant has expired")

        async with self.db.transaction() as connection:
            row = await (
                await connection.execute(
                    """SELECT g.* FROM approval_grants g
                           JOIN human_tasks t ON t.id = g.task_id
                           WHERE g.id = $1 AND t.tenant_id = $2""",
                    (grant_id, context.actor.tenant_id),
                )
            ).fetchone()
            if row["state"] != str(ApprovalGrantState.ACTIVE):
                raise ValueError("Approval grant is not active")
            if row["resource_type"] != "tool" or row["resource_id"] != resource_id:
                raise ValueError("Approval grant does not match the requested tool")
            if row["input_digest"] != digest:
                raise ValueError("Approval grant does not match the tool input")
            cursor = await connection.execute(
                """UPDATE approval_grants
                       SET state = 'consumed', consumed_at = $1
                       WHERE id = $2 AND state = 'active'""",
                (now, grant_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Approval grant was consumed concurrently")
            await self.audit_service.record(
                "approval_grant.consumed",
                resource_type="approval_grant",
                resource_id=grant_id,
                metadata={
                    "workflow_id": row["workflow_id"],
                    "tool_name": resource_id,
                },
                connection=connection,
            )
        return dict(row)

    async def get_active_grant(self, workflow_id: str) -> dict | None:
        context = get_execution_context()
        row = await self.db.fetch_one(
            """SELECT g.* FROM approval_grants g
                   JOIN workflow_runs w ON w.id = g.workflow_id
                   WHERE g.workflow_id = $1 AND g.state = 'active'
                     AND w.tenant_id = $2
                   ORDER BY g.created_at DESC LIMIT 1""",
            (workflow_id, context.actor.tenant_id),
        )
        return dict(row) if row else None

    async def get(self, task_id: str, *, connection=None) -> dict | None:
        context = get_execution_context()
        executor = connection or self.db
        row = await (
            await executor.execute(
                "SELECT * FROM human_tasks WHERE id = $1 AND tenant_id = $2",
                (task_id, context.actor.tenant_id),
            )
        ).fetchone() if connection else await self.db.fetch_one(
            "SELECT * FROM human_tasks WHERE id = $1 AND tenant_id = $2",
            (task_id, context.actor.tenant_id),
        )
        if row is None:
            return None
        result = self._task_dict(row)
        if connection:
            actions = await (
                await connection.execute(
                    """SELECT * FROM human_task_actions
                           WHERE task_id = $1 ORDER BY created_at, id""",
                    (task_id,),
                )
            ).fetchall()
        else:
            actions = await self.db.fetch_all(
                """SELECT * FROM human_task_actions
                       WHERE task_id = $1 ORDER BY created_at, id""",
                (task_id,),
            )
        result["actions"] = [self._action_dict(action) for action in actions]
        return result

    async def list(
        self,
        *,
        state: str | None = None,
        workflow_id: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        context = get_execution_context()
        clauses = ["h.tenant_id = $1"]
        params: list[Any] = [context.actor.tenant_id]
        join = ""
        if session_id:
            join = "JOIN workflow_runs w ON w.id = h.workflow_id"
            clauses.append(f"w.session_id = ${len(params) + 1}")
            params.append(session_id)
        if state:
            clauses.append(f"h.state = ${len(params) + 1}")
            params.append(state)
        if workflow_id:
            clauses.append(f"h.workflow_id = ${len(params) + 1}")
            params.append(workflow_id)
        params.extend([min(max(limit, 1), 500), max(offset, 0)])
        rows = await self.db.fetch_all(
            f"""SELECT h.* FROM human_tasks h {join}
                WHERE {' AND '.join(clauses)}
                ORDER BY h.created_at DESC LIMIT ${len(params) - 1} OFFSET ${len(params)}""",
            tuple(params),
        )
        return [self._task_dict(row) for row in rows]

    async def _append_action(
        self,
        connection,
        task_id: str,
        action: str,
        *,
        comment: str = "",
        idempotency_key: str,
        metadata: dict | None = None,
    ) -> None:
        context = get_execution_context()
        await connection.execute(
            """INSERT INTO human_task_actions
                   (id, task_id, actor_id, action, comment,
                    idempotency_key, metadata, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            (
                new_id(),
                task_id,
                context.actor.actor_id,
                action,
                comment or None,
                idempotency_key,
                canonical_json(metadata or {}),
                self._now(),
            ),
        )

    async def _idempotent_action(self, connection, task_id: str, key: str):
        if not key:
            raise ValueError("Idempotency-Key is required")
        return await (
            await connection.execute(
                """SELECT * FROM human_task_actions
                       WHERE task_id = $1 AND idempotency_key = $2""",
                (task_id, key),
            )
        ).fetchone()

    async def _task_row(self, connection, task_id: str):
        context = get_execution_context()
        return await (
            await connection.execute(
                "SELECT * FROM human_tasks WHERE id = $1 AND tenant_id = $2",
                (task_id, context.actor.tenant_id),
            )
        ).fetchone()

    async def _approval_count(self, connection, task_id: str) -> int:
        row = await (
            await connection.execute(
                """SELECT COUNT(DISTINCT actor_id) AS count
                       FROM human_task_actions
                       WHERE task_id = $1 AND action = 'approve'""",
                (task_id,),
            )
        ).fetchone()
        return row["count"]

    def _claim_expired(self, task, now) -> bool:
        return bool(
            task["claim_expires_at"] and self._is_expired(task["claim_expires_at"], now)
        )

    @staticmethod
    def _task_dict(row) -> dict:
        result = dict(row)
        # human_tasks.metadata is stored as JSON text; expose it as an object so
        # callers can read fields like artifact_version_id.
        if result.get("metadata") and isinstance(result["metadata"], str):
            try:
                result["metadata"] = json.loads(result["metadata"])
            except (json.JSONDecodeError, TypeError):
                result["metadata"] = {}
        elif not result.get("metadata"):
            result["metadata"] = {}
        return result

    @staticmethod
    def _action_dict(row) -> dict:
        result = dict(row)
        result["metadata"] = (
            json.loads(result["metadata"]) if result["metadata"] else {}
        )
        return result

    def _now(self):
        return self._datetime_to_db(datetime.now(timezone.utc))

    def _datetime_to_db(self, value):
        if isinstance(value, str):
            source = datetime.fromisoformat(value)
        else:
            source = value
        if source.tzinfo is None:
            return source
        return source.astimezone(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _datetime_from_db(value) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        return datetime.fromisoformat(value)

    def _is_expired(self, expires_at, now) -> bool:
        return self._datetime_from_db(expires_at) <= self._datetime_from_db(now)

    @staticmethod
    def _event_timestamp(value) -> str:
        if isinstance(value, datetime):
            source = value
            if source.tzinfo is None:
                source = source.replace(tzinfo=timezone.utc)
            else:
                source = source.astimezone(timezone.utc)
            return source.isoformat()
        return value
