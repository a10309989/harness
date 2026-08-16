"""Tests for immutable artifacts and persisted risk policy enforcement."""

import asyncio

import pytest

from harness.artifacts.service import ArtifactService
from harness.artifacts.storage import LocalCAS
from harness.models.artifact import ArtifactDraft, ArtifactType
from harness.models.policy import PolicyEffect, PolicyRuleInput, RiskLevel
from harness.models.tool import ToolResult
from harness.observability.audit import AuditService
from harness.observability.context import (
    ExecutionContext,
    reset_execution_context,
    set_execution_context,
)
from harness.policy.engine import PolicyEngine
from harness.security.models import ActorContext, ActorType
from harness.tools.base import BaseTool
from harness.tools.executor import ToolExecutor
from harness.tools.registry import ToolRegistry


@pytest.fixture
async def phase2(db, tmp_path):
    audit = AuditService(db)
    storage = LocalCAS(tmp_path / "artifacts")
    artifacts = ArtifactService(db, storage, audit)
    policy = PolicyEngine(db, audit)
    await policy.initialize()
    actor = ActorContext(
        actor_id="phase2-user",
        actor_type=ActorType.USER,
        display_name="Phase 2 User",
        roles=("operator",),
        permissions=frozenset({"*"}),
    )
    token = set_execution_context(
        ExecutionContext(trace_id="phase2-trace", span_id="root", actor=actor)
    )
    yield db, audit, storage, artifacts, policy
    reset_execution_context(token)


async def test_local_cas_is_concurrent_and_detects_tampering(phase2):
    _, _, storage, _, _ = phase2
    content = b"same immutable content"

    results = await asyncio.gather(*(storage.put(content) for _ in range(12)))
    assert len({result[0] for result in results}) == 1
    assert len({result[1] for result in results}) == 1

    digest, storage_key = results[0]
    path = storage.root / storage_key
    assert await storage.get(storage_key, digest) == content

    path.write_bytes(b"tampered")
    with pytest.raises(IOError, match="integrity check failed"):
        await storage.get(storage_key, digest)


async def test_artifact_versions_are_append_only_and_lineage_is_version_bound(phase2):
    db, _, _, artifacts, _ = phase2
    first = await artifacts.create(
        ArtifactDraft(
            artifact_type=ArtifactType.TEST_SCRIPT,
            name="checkout test",
            content="print('v1')",
            media_type="text/x-python",
        )
    )
    second = await artifacts.create_version(
        first.artifact_id,
        "print('v2')",
        media_type="text/x-python",
    )
    diagnosis = await artifacts.create(
        ArtifactDraft(
            artifact_type=ArtifactType.DIAGNOSIS,
            name="checkout diagnosis",
            content="The assertion changed.",
        )
    )
    link = await artifacts.link(
        diagnosis.version_id,
        first.version_id,
        "diagnoses",
    )

    assert first.version_number == 1
    assert second.version_number == 2
    assert first.content_digest != second.content_digest
    _, first_content = await artifacts.read_version(first.version_id)
    _, second_content = await artifacts.read_version(second.version_id)
    assert first_content == b"print('v1')"
    assert second_content == b"print('v2')"

    lineage = await artifacts.get_lineage(diagnosis.version_id)
    assert lineage["outgoing"][0]["id"] == link["id"]
    assert lineage["outgoing"][0]["target_version_id"] == first.version_id

    assert await artifacts.archive(first.artifact_id) is True
    with pytest.raises(ValueError, match="Archived"):
        await artifacts.create_version(first.artifact_id, "print('v3')")

    rows = await db.fetch_all(
        "SELECT version_number FROM artifact_versions WHERE artifact_id = $1",
        (first.artifact_id,),
    )
    assert sorted(row["version_number"] for row in rows) == [1, 2]


async def test_default_policy_persists_allow_deny_and_approval(phase2):
    db, _, _, _, policy = phase2

    allowed = await policy.evaluate_tool("file_reader", RiskLevel.LOW, {"path": "x"})
    approval = await policy.evaluate_tool(
        "code_executor",
        RiskLevel.HIGH,
        {"code": "print(1)"},
    )
    denied = await policy.evaluate_tool(
        "dangerous_remote",
        RiskLevel.CRITICAL,
        {},
    )

    assert allowed.decision == PolicyEffect.ALLOW
    assert approval.decision == PolicyEffect.REQUIRE_APPROVAL
    assert denied.decision == PolicyEffect.DENY
    persisted = await db.fetch_all(
        "SELECT decision FROM policy_decisions WHERE trace_id = 'phase2-trace'"
    )
    assert {row["decision"] for row in persisted} == {
        "allow",
        "require_approval",
        "deny",
    }


async def test_two_person_rule_and_tool_interception(phase2):
    db, _, _, _, policy = phase2
    await policy.upsert_rule(
        PolicyRuleInput(
            name="shell-two-person",
            priority=1,
            effect=PolicyEffect.REQUIRE_TWO_PERSON_APPROVAL,
            resource_pattern="controlled_shell",
            risk_levels=[RiskLevel.HIGH],
            reason="Production shell access needs two reviewers.",
        )
    )

    registry = ToolRegistry()
    tool = _ControlledShell()
    registry.register(tool)
    executor = ToolExecutor(registry, policy)
    result = await executor.execute("controlled_shell", {"command": "echo hello"})

    assert result.success is False
    assert result.policy_decision == PolicyEffect.REQUIRE_TWO_PERSON_APPROVAL
    assert result.policy_decision_id
    assert tool.executions == 0
    row = await db.fetch_one(
        "SELECT decision FROM policy_decisions WHERE id = $1",
        (result.policy_decision_id,),
    )
    assert row["decision"] == "require_two_person_approval"


async def test_policy_decision_references_immutable_rule_version(phase2):
    db, _, _, _, policy = phase2
    rule = await policy.upsert_rule(
        PolicyRuleInput(
            name="versioned-rule",
            priority=1,
            effect=PolicyEffect.ALLOW,
            resource_pattern="versioned_tool",
            risk_levels=[RiskLevel.LOW],
            reason="version one",
        )
    )
    first = await policy.evaluate_tool(
        "versioned_tool",
        RiskLevel.LOW,
        {},
    )
    await policy.upsert_rule(
        PolicyRuleInput(
            name="versioned-rule",
            priority=1,
            effect=PolicyEffect.ALLOW,
            resource_pattern="versioned_tool",
            risk_levels=[RiskLevel.LOW],
            reason="version two",
        ),
        rule_id=rule["id"],
    )
    versions = await db.fetch_all(
        """SELECT * FROM policy_rule_versions
           WHERE rule_id = $1 ORDER BY version_number""",
        (rule["id"],),
    )

    assert len(versions) == 2
    assert first.matched_rule_version_id == versions[0]["id"]
    assert first.rule_digest == versions[0]["rule_digest"]


async def test_artifact_reads_are_tenant_scoped(phase2):
    _, _, _, artifacts, _ = phase2
    created = await artifacts.create(
        ArtifactDraft(
            artifact_type=ArtifactType.GENERIC_DOCUMENT,
            name="tenant-a",
            content="private",
        )
    )
    other = ActorContext(
        actor_id="other-user",
        actor_type=ActorType.USER,
        tenant_id="tenant-b",
        roles=("operator",),
        permissions=frozenset({"*"}),
    )
    token = set_execution_context(
        ExecutionContext(trace_id="other-trace", span_id="root", actor=other)
    )
    try:
        assert await artifacts.get(created.artifact_id) is None
        with pytest.raises(KeyError):
            await artifacts.read_version(created.version_id)
    finally:
        reset_execution_context(token)


class _ControlledShell(BaseTool):
    name = "controlled_shell"
    description = "Test-only controlled shell."
    parameters_schema = {"command": {"type": "string", "required": True}}
    risk_level = RiskLevel.HIGH
    risk_tags = frozenset({"shell"})

    def __init__(self) -> None:
        self.executions = 0

    async def execute(self, **kwargs) -> ToolResult:
        self.executions += 1
        return ToolResult(success=True, data={"command": kwargs["command"]})
