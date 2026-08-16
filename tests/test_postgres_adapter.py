import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from harness.db.postgres import PostgresConnection


def test_postgres_adapter_rejects_sqlite_compat_sql():
    with pytest.raises(ValueError, match="native PostgreSQL SQL"):
        PostgresConnection._reject_sqlite_compat(
            "SELECT * FROM sessions WHERE id = ? AND tenant_id = ?"
        )
    with pytest.raises(ValueError, match="native PostgreSQL SQL"):
        PostgresConnection._reject_sqlite_compat(
            "INSERT OR IGNORE INTO roles (name) VALUES ($1)"
        )
    with pytest.raises(ValueError, match="native PostgreSQL SQL"):
        PostgresConnection._reject_sqlite_compat(
            "SELECT * FROM audit_events ORDER BY rowid"
        )


def test_postgres_adapter_accepts_native_sql():
    PostgresConnection._reject_sqlite_compat(
        "SELECT * FROM sessions WHERE id = $1 AND tenant_id = $2"
    )


@pytest.mark.skipif(
    os.getenv("HARNESS_RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires the local PostgreSQL Docker service",
)
async def test_postgres_database_initializes_local_schema():
    from harness.db.postgres import PostgresDatabase

    database = PostgresDatabase(
        "postgresql://harness:harness-local-dev@127.0.0.1:5432/harness"
    )
    await database.initialize()
    try:
        row = await database.fetch_one("SELECT 1 AS ready")
        assert row["ready"] == 1
    finally:
        await database.close()


@pytest.mark.skipif(
    os.getenv("HARNESS_RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires the local PostgreSQL Docker service",
)
async def test_postgres_workflow_checkpoint_and_approval_path(tmp_path):
    from harness.artifacts.service import ArtifactService
    from harness.artifacts.storage import LocalCAS
    from harness.db.postgres import PostgresDatabase
    from harness.models.policy import PolicyDecision, PolicyEffect, RiskLevel
    from harness.observability.audit import AuditService
    from harness.observability.context import (
        ExecutionContext,
        reset_execution_context,
        set_execution_context,
    )
    from harness.security.models import ActorContext, ActorType
    from harness.workflow.context import AgentExecutionFrame
    from harness.workflow.human_tasks import HumanTaskService
    from harness.workflow.service import WorkflowService

    database = PostgresDatabase(
        "postgresql://harness:harness-local-dev@127.0.0.1:5432/harness"
    )
    await database.initialize()
    tenant_id = f"pg-pr9-{uuid4().hex}"
    requester = ActorContext(
        actor_id=f"requester-{tenant_id}",
        actor_type=ActorType.USER,
        display_name="Requester",
        tenant_id=tenant_id,
        roles=("operator",),
        permissions=frozenset({"*"}),
    )
    token = set_execution_context(
        ExecutionContext(
            trace_id=f"trace-{tenant_id}",
            span_id="root",
            session_id="session-pg",
            actor=requester,
        )
    )
    try:
        audit = AuditService(database)
        artifacts = ArtifactService(database, LocalCAS(tmp_path / "artifacts"), audit)
        workflows = WorkflowService(database, artifacts, audit)
        tasks = HumanTaskService(database, workflows, artifacts, audit)
        decision_id = f"decision-{tenant_id}"
        await database.execute(
            """INSERT INTO policy_decisions
               (id, tenant_id, trace_id, span_id, actor_id, resource_type, resource_id,
                risk_level, decision, reason, created_at)
               VALUES ($1, $2, $3, $4, $5, 'tool', 'controlled_tool',
                       'high', 'require_approval', 'PG native approval path', $6)""",
            (
                decision_id,
                tenant_id,
                f"trace-{tenant_id}",
                "root",
                requester.actor_id,
                datetime.now(timezone.utc).replace(tzinfo=None),
            ),
        )
        await database.commit()

        workflow_id = await workflows.create_run(
            session_id="session-pg",
            input_data={"message": "run controlled tool"},
        )
        reset_execution_context(token)
        token = set_execution_context(
            ExecutionContext(
                trace_id=f"trace-{tenant_id}",
                span_id="requester",
                session_id="session-pg",
                workflow_id=workflow_id,
                actor=requester,
            )
        )
        suspended = await workflows.suspend_for_tool(
            policy_decision=PolicyDecision(
                id=decision_id,
                decision=PolicyEffect.REQUIRE_APPROVAL,
                risk_level=RiskLevel.HIGH,
                resource_type="tool",
                resource_id="controlled_tool",
                reason="PG native approval path",
            ),
            tool_name="controlled_tool",
            params={"command": "deploy"},
            risk_level="high",
            idempotency_mode="non_idempotent",
            frame=AgentExecutionFrame(
                agent_id="controlled-agent",
                message="run controlled tool",
                session_id="session-pg",
            ),
        )
        workflow = await workflows.get(workflow_id)
        task = (await tasks.list(workflow_id=workflow_id))[0]
        assert workflow["state"] == "suspended"
        assert task["id"] == suspended.info.human_task_id

        reset_execution_context(token)
        reviewer = ActorContext(
            actor_id=f"reviewer-{tenant_id}",
            actor_type=ActorType.USER,
            display_name="Reviewer",
            tenant_id=tenant_id,
            roles=("reviewer",),
            permissions=frozenset({"*"}),
        )
        token = set_execution_context(
            ExecutionContext(
                trace_id=f"trace-{tenant_id}",
                span_id="reviewer",
                session_id="session-pg",
                workflow_id=workflow_id,
                actor=reviewer,
            )
        )
        approved = await tasks.approve(task["id"], idempotency_key=f"approve-{tenant_id}")
        grant = await tasks.get_active_grant(workflow_id)
        assert approved["state"] == "approved"
        assert grant is not None
        assert (await workflows.get(workflow_id))["state"] == "resume_requested"
    finally:
        reset_execution_context(token)
        await database.close()
