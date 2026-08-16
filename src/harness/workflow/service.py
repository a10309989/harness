"""Durable workflow state, transitions, and suspension checkpoints."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from harness.artifacts.service import ArtifactService
from harness.db.protocols import DatabaseProtocol
from harness.models.artifact import ArtifactDraft, ArtifactType
from harness.models.policy import PolicyDecision, PolicyEffect
from harness.models.workflow import (
    ExecutionSuspended,
    SuspensionInfo,
    WorkflowState,
    WorkflowStepState,
    WorkflowStepType,
)
from harness.observability.audit import AuditService
from harness.observability.context import get_execution_context, new_id
from harness.observability.redaction import canonical_json, content_digest
from harness.workflow.context import AgentExecutionFrame


class WorkflowService:
    """Authoritative state machine for durable executions."""

    TERMINAL_STATES = {
        WorkflowState.COMPLETED,
        WorkflowState.FAILED,
        WorkflowState.CANCELLED,
        WorkflowState.EXPIRED,
    }
    VALID_TRANSITIONS = {
        WorkflowState.CREATED: {
            WorkflowState.RUNNING,
            WorkflowState.CANCELLED,
        },
        WorkflowState.RUNNING: {
            WorkflowState.SUSPENDED,
            WorkflowState.COMPLETED,
            WorkflowState.FAILED,
            WorkflowState.RECOVERY_REQUIRED,
            WorkflowState.CANCELLED,
        },
        WorkflowState.SUSPENDED: {
            WorkflowState.RESUME_REQUESTED,
            WorkflowState.CANCELLED,
            WorkflowState.EXPIRED,
        },
        WorkflowState.RESUME_REQUESTED: {
            WorkflowState.RUNNING,
            WorkflowState.SUSPENDED,
            WorkflowState.RECOVERY_REQUIRED,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        },
        WorkflowState.RECOVERY_REQUIRED: {
            WorkflowState.RESUME_REQUESTED,
            WorkflowState.CANCELLED,
            WorkflowState.FAILED,
        },
    }

    def __init__(
        self,
        db: DatabaseProtocol,
        artifact_service: ArtifactService,
        audit_service: AuditService,
    ) -> None:
        self.db = db
        self.artifact_service = artifact_service
        self.audit_service = audit_service

    async def create_run(
        self,
        *,
        session_id: str | None,
        input_data: Any,
        agent_id: str | None = None,
        parent_workflow_id: str | None = None,
    ) -> str:
        context = get_execution_context()
        workflow_id = new_id()
        now = self._now()
        async with self.db.transaction() as connection:
            await connection.execute(
                """INSERT INTO workflow_runs
                       (id, tenant_id, session_id, trace_id, state, version,
                        input_digest, created_by, created_at, updated_at,
                        agent_id, parent_workflow_id)
                       VALUES ($1, $2, $3, $4, $5, 1, $6, $7, $8, $9, $10, $11)""",
                (
                    workflow_id,
                    context.actor.tenant_id,
                    session_id,
                    context.trace_id,
                    str(WorkflowState.CREATED),
                    content_digest(input_data),
                    context.actor.actor_id,
                    now,
                    now,
                    agent_id,
                    parent_workflow_id,
                ),
            )
            await self._append_transition(
                connection,
                workflow_id,
                None,
                WorkflowState.CREATED,
                "workflow created",
            )
            await self._transition(
                connection,
                workflow_id,
                WorkflowState.CREATED,
                WorkflowState.RUNNING,
                "execution started",
            )
            await self.audit_service.record(
                "workflow.started",
                resource_type="workflow",
                resource_id=workflow_id,
                input_data=input_data,
                metadata={"session_id": session_id},
                connection=connection,
            )
        return workflow_id

    async def suspend_for_tool(
        self,
        *,
        policy_decision: PolicyDecision,
        tool_name: str,
        params: dict[str, Any],
        risk_level: str,
        idempotency_mode: str,
        frame: AgentExecutionFrame | None,
    ) -> ExecutionSuspended:
        context = get_execution_context()
        if context.workflow_id is None:
            raise RuntimeError("A workflow context is required for durable suspension")
        workflow_id = context.workflow_id
        step_id = new_id()
        checkpoint_id = new_id()
        task_id = new_id()
        required_approvals = (
            2
            if policy_decision.decision
            == PolicyEffect.REQUIRE_TWO_PERSON_APPROVAL
            else 1
        )
        checkpoint_payload = {
            "schema_version": 1,
            "kind": "tool_call",
            "workflow_id": workflow_id,
            "session_id": frame.session_id if frame else context.session_id,
            "agent_id": frame.agent_id if frame else None,
            "original_message": frame.message if frame else None,
            "tool_name": tool_name,
            "params": params,
            "risk_level": risk_level,
            "idempotency_mode": idempotency_mode,
            "policy_decision_id": policy_decision.id,
        }
        now = self._now()
        async with self.db.transaction() as connection:
            row = await self._workflow_row(connection, workflow_id)
            if row is None:
                raise KeyError(f"Workflow '{workflow_id}' not found")
            state = WorkflowState(row["state"])
            if state not in {WorkflowState.RUNNING, WorkflowState.RESUME_REQUESTED}:
                raise ValueError(f"Workflow cannot suspend from state '{state}'")
            checkpoint_artifact = await self.artifact_service.create(
                ArtifactDraft(
                    artifact_type=ArtifactType.WORKFLOW_CHECKPOINT,
                    name=f"Workflow {workflow_id} tool checkpoint",
                    content=canonical_json(checkpoint_payload),
                    media_type="application/json",
                    metadata={
                        "workflow_id": workflow_id,
                        "tool_name": tool_name,
                        "policy_decision_id": policy_decision.id,
                    },
                ),
                connection=connection,
            )
            sequence_number = await self._next_sequence(connection, workflow_id)
            await connection.execute(
                """INSERT INTO workflow_steps
                       (id, workflow_id, sequence_number, step_type, agent_id,
                        resource_id, state, input_digest, created_at, started_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
                (
                    step_id,
                    workflow_id,
                    sequence_number,
                    str(WorkflowStepType.TOOL),
                    frame.agent_id if frame else None,
                    tool_name,
                    str(WorkflowStepState.WAITING),
                    content_digest(params),
                    now,
                    now,
                ),
            )
            await connection.execute(
                """INSERT INTO workflow_checkpoints
                       (id, workflow_id, step_id, artifact_version_id,
                        policy_decision_id, checkpoint_type, created_at)
                       VALUES ($1, $2, $3, $4, $5, 'tool_call', $6)""",
                (
                    checkpoint_id,
                    workflow_id,
                    step_id,
                    checkpoint_artifact.version_id,
                    policy_decision.id,
                    now,
                ),
            )
            await connection.execute(
                """INSERT INTO human_tasks
                       (id, tenant_id, workflow_id, checkpoint_id,
                        policy_decision_id, title, description, state,
                        required_approvals, requester_id, version,
                        created_at, updated_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending', $8, $9, 1, $10, $11)""",
                (
                    task_id,
                    context.actor.tenant_id,
                    workflow_id,
                    checkpoint_id,
                    policy_decision.id,
                    f"Approve tool execution: {tool_name}",
                    policy_decision.reason,
                    required_approvals,
                    context.actor.actor_id,
                    now,
                    now,
                ),
            )
            await connection.execute(
                """UPDATE workflow_runs
                       SET current_step_id = $1 WHERE id = $2""",
                (step_id, workflow_id),
            )
            await self._transition(
                connection,
                workflow_id,
                state,
                WorkflowState.SUSPENDED,
                "human approval required",
                metadata={
                    "human_task_id": task_id,
                    "checkpoint_id": checkpoint_id,
                    "policy_decision_id": policy_decision.id,
                },
            )
            await self.audit_service.record(
                "workflow.suspended",
                resource_type="workflow",
                resource_id=workflow_id,
                decision=str(policy_decision.decision),
                reason=policy_decision.reason,
                metadata={
                    "step_id": step_id,
                    "checkpoint_id": checkpoint_id,
                    "human_task_id": task_id,
                },
                connection=connection,
            )
            await self.audit_service.enqueue(
                "workflow.suspended",
                {
                    "id": new_id(),
                    "event_type": "workflow.suspended",
                    "workflow_id": workflow_id,
                    "human_task_id": task_id,
                    "checkpoint_id": checkpoint_id,
                    "session_id": context.session_id,
                    "created_at": self._event_timestamp(now),
                },
                connection=connection,
            )
        info = SuspensionInfo(
            workflow_id=workflow_id,
            step_id=step_id,
            checkpoint_id=checkpoint_id,
            human_task_id=task_id,
            policy_decision_id=policy_decision.id,
            required_approvals=required_approvals,
        )
        return ExecutionSuspended(info)

    async def save_execution_checkpoint(self, workflow_id: str, payload: dict) -> str:
        """Persist a generic execution resume-point (e.g. an interrupted plan).

        Used by the master's durable PlanExecutor: when a plan node suspends for
        human approval, the plan + completed node outputs are checkpointed so an
        approved resume continues the remaining DAG. The step_id is reused from
        the most recent checkpoint (a local tool suspension) or a placeholder
        step is created when the suspension happened remotely.
        """
        now = self._now()
        checkpoint_id = new_id()
        latest = await self.db.fetch_one(
            "SELECT step_id FROM workflow_checkpoints WHERE workflow_id = $1 "
            "ORDER BY created_at DESC LIMIT 1",
            (workflow_id,),
        )
        async with self.db.transaction() as connection:
            step_id = latest["step_id"] if latest else None
            if step_id is None:
                sequence_number = await self._next_sequence(connection, workflow_id)
                step_id = new_id()
                await connection.execute(
                    """INSERT INTO workflow_steps
                       (id, workflow_id, sequence_number, step_type, agent_id,
                        resource_id, state, created_at, started_at)
                       VALUES ($1, $2, $3, 'agent', $4, 'plan', 'running', $5, $5)""",
                    (
                        step_id,
                        workflow_id,
                        sequence_number,
                        payload.get("suspended_agent_id"),
                        now,
                    ),
                )
            checkpoint_artifact = await self.artifact_service.create(
                ArtifactDraft(
                    artifact_type=ArtifactType.WORKFLOW_CHECKPOINT,
                    name=f"Workflow {workflow_id} execution checkpoint",
                    content=canonical_json(payload),
                    media_type="application/json",
                    metadata={"workflow_id": workflow_id, "checkpoint_type": "plan_execution"},
                ),
                connection=connection,
            )
            await connection.execute(
                """INSERT INTO workflow_checkpoints
                   (id, workflow_id, step_id, artifact_version_id,
                    checkpoint_type, created_at)
                   VALUES ($1, $2, $3, $4, 'plan_execution', $5)""",
                (
                    checkpoint_id,
                    workflow_id,
                    step_id,
                    checkpoint_artifact.version_id,
                    now,
                ),
            )
        return checkpoint_id

    async def suspend(self, workflow_id: str, reason: str = "workflow suspended") -> None:
        """Move a RUNNING workflow to SUSPENDED.

        Used when a plan node re-suspends during an approved resume (e.g. a
        remote node's HITL is still pending) — the workflow leaves RESUME_REQUESTED
        for RUNNING then back to SUSPENDED so a later approval can re-drive it.
        """
        row = await self.get(workflow_id)
        if row is None:
            raise KeyError(f"Workflow '{workflow_id}' not found")
        state = WorkflowState(row["state"])
        if state in self.TERMINAL_STATES or state == WorkflowState.SUSPENDED:
            return
        async with self.db.transaction() as connection:
            await self._transition(
                connection,
                workflow_id,
                state,
                WorkflowState.SUSPENDED,
                reason,
            )

    async def complete(self, workflow_id: str, output_data: Any) -> None:
        await self._finish(
            workflow_id,
            WorkflowState.COMPLETED,
            output_data=output_data,
            reason="workflow completed",
        )

    async def fail(self, workflow_id: str, error: str) -> None:
        await self._finish(
            workflow_id,
            WorkflowState.FAILED,
            error=error,
            reason="workflow failed",
        )

    async def recovery_required(
        self,
        workflow_id: str,
        error: str,
        *,
        internal: bool = False,
    ) -> None:
        row = await (self.get_internal(workflow_id) if internal else self.get(workflow_id))
        if row is None:
            raise KeyError(f"Workflow '{workflow_id}' not found")
        async with self.db.transaction() as connection:
            await connection.execute(
                "UPDATE workflow_runs SET error = $1 WHERE id = $2",
                (error, workflow_id),
            )
            await self._transition(
                connection,
                workflow_id,
                WorkflowState(row["state"]),
                WorkflowState.RECOVERY_REQUIRED,
                error,
            )

    async def cancel(self, workflow_id: str, reason: str = "cancelled") -> bool:
        row = await self.get(workflow_id)
        if row is None:
            return False
        state = WorkflowState(row["state"])
        if state in self.TERMINAL_STATES:
            return False
        async with self.db.transaction() as connection:
            await self._transition(
                connection,
                workflow_id,
                state,
                WorkflowState.CANCELLED,
                reason,
            )
            await connection.execute(
                """UPDATE human_tasks SET state = 'cancelled',
                       completed_at = $1, updated_at = $2, version = version + 1
                       WHERE workflow_id = $3
                         AND state IN ('pending', 'claimed', 'partially_approved')""",
                (self._now(), self._now(), workflow_id),
            )
            await self.audit_service.record(
                "workflow.cancelled",
                resource_type="workflow",
                resource_id=workflow_id,
                reason=reason,
                connection=connection,
            )
        return True

    async def request_resume(
        self,
        workflow_id: str,
        *,
        reason: str = "resume requested",
        connection=None,
    ) -> None:
        owns_transaction = connection is None
        if owns_transaction:
            async with self.db.transaction() as transaction:
                await self.request_resume(
                    workflow_id,
                    reason=reason,
                    connection=transaction,
                )
            return
        row = await self._workflow_row(connection, workflow_id)
        if row is None:
            raise KeyError(f"Workflow '{workflow_id}' not found")
        state = WorkflowState(row["state"])
        if state == WorkflowState.RESUME_REQUESTED:
            return
        if state not in {
            WorkflowState.SUSPENDED,
            WorkflowState.RECOVERY_REQUIRED,
        }:
            raise ValueError(f"Workflow cannot resume from state '{state}'")
        await self._transition(
            connection,
            workflow_id,
            state,
            WorkflowState.RESUME_REQUESTED,
            reason,
        )
        now = self._now()
        await self.audit_service.enqueue(
            "workflow.resume_requested",
            {
                "id": new_id(),
                "event_type": "workflow.resume_requested",
                "workflow_id": workflow_id,
                "created_at": self._event_timestamp(now),
            },
            connection=connection,
        )

    async def get(self, workflow_id: str) -> dict | None:
        context = get_execution_context()
        return await self._get(workflow_id, tenant_id=context.actor.tenant_id)

    async def get_internal(self, workflow_id: str) -> dict | None:
        """Read a workflow for a trusted worker before tenant context is restored."""
        return await self._get(workflow_id, tenant_id=None)

    async def list_running(self, agent_id: str | None = None, limit: int = 50) -> list[str]:
        """List RUNNING workflow ids (optionally scoped to an owning agent).

        Used by the stateless master's WorkflowDriver to pick up submitted
        plan-runs to execute.
        """
        if agent_id is None:
            rows = await self.db.fetch_all(
                "SELECT id FROM workflow_runs WHERE state = 'running' "
                "ORDER BY updated_at LIMIT $1",
                (limit,),
            )
        else:
            rows = await self.db.fetch_all(
                "SELECT id FROM workflow_runs WHERE state = 'running' AND agent_id = $1 "
                "ORDER BY updated_at LIMIT $2",
                (agent_id, limit),
            )
        return [row["id"] for row in rows]

    async def list_suspended(self, agent_id: str | None = None, limit: int = 50) -> list[str]:
        """List SUSPENDED workflow ids (optionally scoped to an owning agent).

        The stateless master re-drives suspended plan-runs until the remote
        node's HITL resolves (cross-service cascade via polling).
        """
        if agent_id is None:
            rows = await self.db.fetch_all(
                "SELECT id FROM workflow_runs WHERE state = 'suspended' "
                "ORDER BY updated_at LIMIT $1",
                (limit,),
            )
        else:
            rows = await self.db.fetch_all(
                "SELECT id FROM workflow_runs WHERE state = 'suspended' AND agent_id = $1 "
                "ORDER BY updated_at LIMIT $2",
                (agent_id, limit),
            )
        return [row["id"] for row in rows]

    async def update_output_data(self, workflow_id: str, output_data: Any) -> None:
        """Persist arbitrary run state (e.g. a plan-run's plan + node progress)."""
        await self.db.execute(
            "UPDATE workflow_runs SET output_data = $1, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = $2",
            (canonical_json(output_data), workflow_id),
        )
        await self.db.commit()

    async def project_temporal_run(
        self,
        workflow_id: str,
        *,
        temporal_workflow_id: str,
        temporal_run_id: str | None,
        temporal_state: str,
        execution_engine: str,
    ) -> None:
        """Persist Temporal state as a read-model projection of a legacy run."""
        now = self._now()
        cursor = await self.db.execute(
            """UPDATE workflow_runs
                   SET temporal_workflow_id = $1, temporal_run_id = $2, temporal_state = $3,
                       temporal_updated_at = $4, execution_engine = $5, updated_at = $6
                   WHERE id = $7""",
            (
                temporal_workflow_id,
                temporal_run_id,
                temporal_state,
                now,
                execution_engine,
                now,
                workflow_id,
            ),
        )
        await self.db.commit()
        if cursor.rowcount == 0:
            raise KeyError(f"Workflow '{workflow_id}' not found")

    async def update_temporal_state(self, workflow_id: str, temporal_state: str) -> None:
        """Update a Temporal projection without discarding its execution IDs."""
        cursor = await self.db.execute(
            """UPDATE workflow_runs
                   SET temporal_state = $1, temporal_updated_at = $2, updated_at = $2
                   WHERE id = $3""",
            (temporal_state, self._now(), workflow_id),
        )
        await self.db.commit()
        if cursor.rowcount == 0:
            raise KeyError(f"Workflow '{workflow_id}' not found")

    async def _get(
        self,
        workflow_id: str,
        *,
        tenant_id: str | None,
    ) -> dict | None:
        clauses = ["id = $1"]
        params: list[Any] = [workflow_id]
        if tenant_id is not None:
            clauses.append(f"tenant_id = ${len(params) + 1}")
            params.append(tenant_id)
        row = await self.db.fetch_one(
            f"SELECT * FROM workflow_runs WHERE {' AND '.join(clauses)}",
            tuple(params),
        )
        if row is None:
            return None
        result = self._run_dict(row)
        steps = await self.db.fetch_all(
            """SELECT * FROM workflow_steps WHERE workflow_id = $1
                   ORDER BY sequence_number""",
            (workflow_id,),
        )
        transitions = await self.db.fetch_all(
            """SELECT * FROM workflow_transitions WHERE workflow_id = $1
                   ORDER BY created_at, id""",
            (workflow_id,),
        )
        result["steps"] = [self._step_dict(item) for item in steps]
        result["transitions"] = [self._transition_dict(item) for item in transitions]
        return result

    async def list(
        self,
        *,
        state: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        context = get_execution_context()
        clauses = ["tenant_id = $1"]
        params: list[Any] = [context.actor.tenant_id]
        if state:
            clauses.append(f"state = ${len(params) + 1}")
            params.append(state)
        if session_id:
            clauses.append(f"session_id = ${len(params) + 1}")
            params.append(session_id)
        params.extend([min(max(limit, 1), 500), max(offset, 0)])
        rows = await self.db.fetch_all(
            f"""SELECT * FROM workflow_runs WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC LIMIT ${len(params) - 1} OFFSET ${len(params)}""",
            tuple(params),
        )
        return [self._run_dict(row) for row in rows]

    async def load_checkpoint(self, workflow_id: str) -> tuple[dict, dict]:
        context = get_execution_context()
        row = await self.db.fetch_one(
            """SELECT c.*, s.resource_id, s.state AS step_state
                   FROM workflow_checkpoints c
                   JOIN workflow_steps s ON s.id = c.step_id
                   JOIN workflow_runs w ON w.id = c.workflow_id
                   WHERE c.workflow_id = $1 AND w.tenant_id = $2
                   ORDER BY c.created_at DESC LIMIT 1""",
            (workflow_id, context.actor.tenant_id),
        )
        if row is None:
            raise KeyError(f"No checkpoint for workflow '{workflow_id}'")
        _, content = await self.artifact_service.read_version(
            row["artifact_version_id"]
        )
        return dict(row), json.loads(content.decode("utf-8"))

    async def load_checkpoint_by_id(self, checkpoint_id: str) -> tuple[dict, dict]:
        context = get_execution_context()
        row = await self.db.fetch_one(
            """SELECT c.*, s.resource_id, s.state AS step_state
                   FROM workflow_checkpoints c
                   JOIN workflow_steps s ON s.id = c.step_id
                   JOIN workflow_runs w ON w.id = c.workflow_id
                   WHERE c.id = $1 AND w.tenant_id = $2""",
            (checkpoint_id, context.actor.tenant_id),
        )
        if row is None:
            raise KeyError(f"Checkpoint '{checkpoint_id}' not found")
        _, content = await self.artifact_service.read_version(
            row["artifact_version_id"]
        )
        return dict(row), json.loads(content.decode("utf-8"))

    async def recover_interrupted_runs(self) -> int:
        """Move runs left in RUNNING by a prior process into manual recovery."""
        rows = await self.db.fetch_all(
            "SELECT id, output_data FROM workflow_runs WHERE state = 'running'"
        )
        recovered = 0
        for row in rows:
            # Pipelines are frontend-driven: they are resumed explicitly via
            # POST /pipelines/{id}/resume after each approval, and marking an
            # in-flight stage recovery_required would strand it. Skip them.
            if row.get("output_data"):
                try:
                    maybe = json.loads(row["output_data"])
                    if maybe.get("kind") == "pipeline":
                        continue
                except (ValueError, TypeError):
                    pass
            try:
                await self.recovery_required(
                    row["id"],
                    "Process restarted while workflow was running",
                    internal=True,
                )
                recovered += 1
            except (ValueError, RuntimeError):
                continue
        return recovered

    async def mark_step_running(self, step_id: str) -> None:
        await self.db.execute(
            """UPDATE workflow_steps SET state = 'running', started_at = $1
                   WHERE id = $2 AND state = 'waiting'""",
            (self._now(), step_id),
        )
        await self.db.commit()

    async def mark_step_completed(self, step_id: str, output_data: Any) -> None:
        await self.db.execute(
            """UPDATE workflow_steps
                   SET state = 'completed', output_data = $1, completed_at = $2
                   WHERE id = $3""",
            (canonical_json(output_data), self._now(), step_id),
        )
        await self.db.commit()

    async def acquire_resume_lease(
        self,
        workflow_id: str,
        owner: str,
        expires_at: str,
    ) -> bool:
        now = self._now()
        cursor = await self.db.execute(
            """UPDATE workflow_runs
                   SET lease_owner = $1, lease_expires_at = $2, updated_at = $3
                   WHERE id = $4 AND state = 'resume_requested'
                     AND (lease_expires_at IS NULL OR lease_expires_at < $5)""",
            (owner, self._datetime_to_db(expires_at), now, workflow_id, now),
        )
        await self.db.commit()
        return cursor.rowcount == 1

    async def release_lease(self, workflow_id: str, owner: str) -> None:
        await self.db.execute(
            """UPDATE workflow_runs
                   SET lease_owner = NULL, lease_expires_at = NULL
                   WHERE id = $1 AND lease_owner = $2""",
            (workflow_id, owner),
        )
        await self.db.commit()

    async def list_resume_requested(self, limit: int = 20) -> list[str]:
        rows = await self.db.fetch_all(
            """SELECT id FROM workflow_runs
                   WHERE state = 'resume_requested'
                   ORDER BY updated_at LIMIT $1""",
            (limit,),
        )
        return [row["id"] for row in rows]

    async def mark_resuming(self, workflow_id: str) -> None:
        async with self.db.transaction() as connection:
            await self._transition(
                connection,
                workflow_id,
                WorkflowState.RESUME_REQUESTED,
                WorkflowState.RUNNING,
                "resume worker started",
            )

    async def _finish(
        self,
        workflow_id: str,
        target: WorkflowState,
        *,
        output_data: Any = None,
        error: str | None = None,
        reason: str,
    ) -> None:
        row = await self.get(workflow_id)
        if row is None:
            raise KeyError(f"Workflow '{workflow_id}' not found")
        state = WorkflowState(row["state"])
        if state == target:
            return
        async with self.db.transaction() as connection:
            await connection.execute(
                """UPDATE workflow_runs SET output_data = $1, error = $2
                       WHERE id = $3""",
                (
                    canonical_json(output_data) if output_data is not None else None,
                    error,
                    workflow_id,
                ),
            )
            await self._transition(
                connection,
                workflow_id,
                state,
                target,
                reason,
            )
            await self.audit_service.record(
                f"workflow.{target}",
                resource_type="workflow",
                resource_id=workflow_id,
                decision="success" if target == WorkflowState.COMPLETED else "failure",
                reason=error,
                output_data=output_data,
                connection=connection,
            )

    async def _transition(
        self,
        connection,
        workflow_id: str,
        from_state: WorkflowState,
        to_state: WorkflowState,
        reason: str,
        metadata: dict | None = None,
    ) -> None:
        if to_state not in self.VALID_TRANSITIONS.get(from_state, set()):
            raise ValueError(
                f"Invalid workflow transition: {from_state} -> {to_state}"
            )
        now = self._now()
        cursor = await connection.execute(
            """UPDATE workflow_runs
                   SET state = $1, version = version + 1, updated_at = $2,
                       completed_at = CASE WHEN $3 IN
                           ('completed', 'failed', 'cancelled', 'expired')
                           THEN $4 ELSE completed_at END
                   WHERE id = $5 AND state = $6""",
            (
                str(to_state),
                now,
                str(to_state),
                now,
                workflow_id,
                str(from_state),
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Workflow state changed concurrently")
        await self._append_transition(
            connection,
            workflow_id,
            from_state,
            to_state,
            reason,
            metadata,
        )

    async def _append_transition(
        self,
        connection,
        workflow_id: str,
        from_state: WorkflowState | None,
        to_state: WorkflowState,
        reason: str,
        metadata: dict | None = None,
    ) -> None:
        context = get_execution_context()
        await connection.execute(
            """INSERT INTO workflow_transitions
                   (id, workflow_id, from_state, to_state, reason,
                    actor_id, metadata, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            (
                new_id(),
                workflow_id,
                str(from_state) if from_state else None,
                str(to_state),
                reason,
                context.actor.actor_id,
                canonical_json(metadata or {}),
                self._now(),
            ),
        )

    async def _workflow_row(self, connection, workflow_id: str):
        return await (
            await connection.execute(
                "SELECT * FROM workflow_runs WHERE id = $1",
                (workflow_id,),
            )
        ).fetchone()

    async def _next_sequence(self, connection, workflow_id: str) -> int:
        row = await (
            await connection.execute(
                """SELECT COALESCE(MAX(sequence_number), 0) AS latest
                       FROM workflow_steps WHERE workflow_id = $1""",
                (workflow_id,),
            )
        ).fetchone()
        return row["latest"] + 1

    @staticmethod
    def _run_dict(row) -> dict:
        result = dict(row)
        result["output_data"] = (
            json.loads(result["output_data"]) if result["output_data"] else None
        )
        return result

    @staticmethod
    def _step_dict(row) -> dict:
        result = dict(row)
        result["output_data"] = (
            json.loads(result["output_data"]) if result["output_data"] else None
        )
        return result

    @staticmethod
    def _transition_dict(row) -> dict:
        result = dict(row)
        result["metadata"] = (
            json.loads(result["metadata"]) if result["metadata"] else {}
        )
        return result

    def _now(self):
        return self._datetime_to_db(datetime.now(UTC))

    def _datetime_to_db(self, value):
        if isinstance(value, str):
            source = datetime.fromisoformat(value)
        else:
            source = value
        if source.tzinfo is None:
            return source
        return source.astimezone(UTC).replace(tzinfo=None)

    @staticmethod
    def _event_timestamp(value) -> str:
        if isinstance(value, datetime):
            source = value
            if source.tzinfo is None:
                source = source.replace(tzinfo=UTC)
            else:
                source = source.astimezone(UTC)
            return source.isoformat()
        return value
