"""Deterministic risk policy compiler and decision engine."""

from __future__ import annotations

import fnmatch
import json
from datetime import datetime, timezone
from typing import Any

from harness.db.protocols import DatabaseProtocol
from harness.models.policy import (
    PolicyDecision,
    PolicyEffect,
    PolicyRuleInput,
    RiskLevel,
)
from harness.observability.audit import AuditService
from harness.observability.context import get_execution_context, new_id
from harness.observability.redaction import canonical_json, content_digest


class PolicyEngine:
    """Evaluates ordered persisted rules and records every decision."""

    DEFAULT_RULES = (
        PolicyRuleInput(
            name="default-critical-deny",
            priority=10,
            effect=PolicyEffect.DENY,
            risk_levels=[RiskLevel.CRITICAL],
            reason="Critical-risk tools are denied by default.",
        ),
        PolicyRuleInput(
            name="default-high-approval",
            priority=20,
            effect=PolicyEffect.REQUIRE_APPROVAL,
            risk_levels=[RiskLevel.HIGH],
            reason="High-risk tools require human approval.",
        ),
        PolicyRuleInput(
            name="default-medium-allow",
            priority=30,
            effect=PolicyEffect.ALLOW,
            risk_levels=[RiskLevel.MEDIUM],
            reason="Medium-risk tools are allowed by the baseline policy.",
        ),
        PolicyRuleInput(
            name="default-low-allow",
            priority=40,
            effect=PolicyEffect.ALLOW,
            risk_levels=[RiskLevel.LOW],
            reason="Low-risk tools are allowed by the baseline policy.",
        ),
    )

    def __init__(
        self,
        db: DatabaseProtocol,
        audit_service: AuditService | None = None,
    ) -> None:
        self.db = db
        self.audit_service = audit_service

    async def initialize(self) -> None:
        for rule in self.DEFAULT_RULES:
            await self.upsert_rule(rule, preserve_existing=True)

    async def evaluate_tool(
        self,
        tool_name: str,
        risk_level: RiskLevel | str,
        params: dict[str, Any],
        *,
        risk_tags: set[str] | frozenset[str] | None = None,
    ) -> PolicyDecision:
        return await self.evaluate(
            resource_type="tool",
            resource_id=tool_name,
            risk_level=RiskLevel(risk_level),
            input_data=params,
            attributes={"risk_tags": sorted(risk_tags or set())},
        )

    async def evaluate(
        self,
        *,
        resource_type: str,
        resource_id: str,
        risk_level: RiskLevel,
        input_data: Any = None,
        attributes: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        context = get_execution_context()
        rows = await self.db.fetch_all(
            """SELECT * FROM policy_rules
                   WHERE enabled = 1 AND resource_type = $1
                   ORDER BY priority ASC, id ASC""",
            (resource_type,),
        )
        matched = next(
            (
                row
                for row in rows
                if self._matches(
                    row,
                    resource_id=resource_id,
                    risk_level=risk_level,
                    actor_roles=set(context.actor.roles),
                    attributes=attributes or {},
                )
            ),
            None,
        )
        if matched:
            effect = PolicyEffect(matched["effect"])
            reason = matched["reason"] or f"Matched policy rule '{matched['name']}'"
            matched_rule_id = matched["id"]
            rule_version = await self._latest_rule_version(matched_rule_id)
            matched_rule_version_id = (
                rule_version["id"] if rule_version is not None else None
            )
            rule_digest = (
                rule_version["rule_digest"] if rule_version is not None else None
            )
        else:
            effect = PolicyEffect.DENY
            reason = "No policy rule matched; fail-closed default applied."
            matched_rule_id = None
            matched_rule_version_id = None
            rule_digest = None

        decision = PolicyDecision(
            id=new_id(),
            decision=effect,
            risk_level=risk_level,
            resource_type=resource_type,
            resource_id=resource_id,
            matched_rule_id=matched_rule_id,
            matched_rule_version_id=matched_rule_version_id,
            rule_digest=rule_digest,
            reason=reason,
        )
        now = self._now()
        persisted_context = {
            "actor_roles": list(context.actor.roles),
            "attributes": attributes or {},
        }
        async with self.db.transaction() as connection:
            await connection.execute(
                """INSERT INTO policy_decisions
                       (id, tenant_id, trace_id, span_id, actor_id, resource_type,
                        resource_id, risk_level, decision, matched_rule_id,
                        matched_rule_version_id, rule_digest, reason,
                        input_digest, context_data, created_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)""",
                (
                    decision.id,
                    context.actor.tenant_id,
                    context.trace_id,
                    context.span_id,
                    context.actor.actor_id,
                    resource_type,
                    resource_id,
                    str(risk_level),
                    str(effect),
                    matched_rule_id,
                    matched_rule_version_id,
                    rule_digest,
                    reason,
                    content_digest(input_data) if input_data is not None else None,
                    canonical_json(persisted_context),
                    now,
                ),
            )
            if self.audit_service:
                await self.audit_service.record(
                    "policy.decision",
                    resource_type=resource_type,
                    resource_id=resource_id,
                    decision=str(effect),
                    reason=reason,
                    input_data=input_data,
                    metadata={
                        "policy_decision_id": decision.id,
                        "matched_rule_id": matched_rule_id,
                        "matched_rule_version_id": matched_rule_version_id,
                        "rule_digest": rule_digest,
                        "risk_level": str(risk_level),
                    },
                    connection=connection,
                )
        return decision

    async def upsert_rule(
        self,
        rule: PolicyRuleInput,
        *,
        rule_id: str | None = None,
        preserve_existing: bool = False,
    ) -> dict:
        existing = await self.db.fetch_one(
            "SELECT id FROM policy_rules WHERE name = $1",
            (rule.name,),
        )
        if preserve_existing and existing:
            await self._snapshot_rule_version(existing["id"])
            return await self.get_rule(existing["id"])
        resolved_id = rule_id or (existing["id"] if existing else new_id())
        now = self._now()
        await self.db.execute(
            """INSERT INTO policy_rules
                   (id, name, priority, enabled, effect, resource_type,
                    resource_pattern, risk_levels, actor_roles, conditions,
                    reason, created_at, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                   ON CONFLICT(id) DO UPDATE SET
                       name = excluded.name,
                       priority = excluded.priority,
                       enabled = excluded.enabled,
                       effect = excluded.effect,
                       resource_type = excluded.resource_type,
                       resource_pattern = excluded.resource_pattern,
                       risk_levels = excluded.risk_levels,
                       actor_roles = excluded.actor_roles,
                       conditions = excluded.conditions,
                       reason = excluded.reason,
                       updated_at = excluded.updated_at""",
            (
                resolved_id,
                rule.name,
                rule.priority,
                int(rule.enabled),
                str(rule.effect),
                rule.resource_type,
                rule.resource_pattern,
                canonical_json([str(value) for value in rule.risk_levels]),
                canonical_json(rule.actor_roles),
                canonical_json(rule.conditions),
                rule.reason,
                now,
                now,
            ),
        )
        await self.db.commit()
        await self._snapshot_rule_version(resolved_id)
        return await self.get_rule(resolved_id)

    async def _snapshot_rule_version(self, rule_id: str) -> dict:
        rule = await self.get_rule(rule_id)
        if rule is None:
            raise KeyError(f"Policy rule '{rule_id}' not found")
        snapshot_data = {
            key: rule[key]
            for key in (
                "id",
                "name",
                "priority",
                "enabled",
                "effect",
                "resource_type",
                "resource_pattern",
                "risk_levels",
                "actor_roles",
                "conditions",
                "reason",
            )
        }
        snapshot = canonical_json(snapshot_data)
        digest = content_digest(snapshot_data)
        existing = await self.db.fetch_one(
            """SELECT * FROM policy_rule_versions
                   WHERE rule_id = $1 AND rule_digest = $2""",
            (rule_id, digest),
        )
        if existing is not None:
            return dict(existing)
        latest = await self.db.fetch_one(
            """SELECT COALESCE(MAX(version_number), 0) AS latest
                   FROM policy_rule_versions WHERE rule_id = $1""",
            (rule_id,),
        )
        version_id = new_id()
        await self.db.execute(
            """INSERT INTO policy_rule_versions
                   (id, rule_id, version_number, rule_digest, snapshot, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
            (
                version_id,
                rule_id,
                latest["latest"] + 1,
                digest,
                snapshot,
                self._now(),
            ),
        )
        await self.db.commit()
        return {
            "id": version_id,
            "rule_id": rule_id,
            "version_number": latest["latest"] + 1,
            "rule_digest": digest,
            "snapshot": snapshot,
        }

    async def _latest_rule_version(self, rule_id: str):
        return await self.db.fetch_one(
            """SELECT * FROM policy_rule_versions
                   WHERE rule_id = $1
                   ORDER BY version_number DESC LIMIT 1""",
            (rule_id,),
        )

    async def get_rule(self, rule_id: str) -> dict | None:
        row = await self.db.fetch_one(
            "SELECT * FROM policy_rules WHERE id = $1",
            (rule_id,),
        )
        return self._rule_dict(row) if row else None

    async def list_rules(self) -> list[dict]:
        rows = await self.db.fetch_all(
            "SELECT * FROM policy_rules ORDER BY priority ASC, name ASC"
        )
        return [self._rule_dict(row) for row in rows]

    async def list_decisions(
        self,
        *,
        trace_id: str | None = None,
        resource_id: str | None = None,
        decision: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        context = get_execution_context()
        clauses: list[str] = ["tenant_id = $1"]
        params: list[Any] = [context.actor.tenant_id]
        for column, value in {
            "trace_id": trace_id,
            "resource_id": resource_id,
            "decision": decision,
        }.items():
            if value is not None:
                clauses.append(f"{column} = ${len(params) + 1}")
                params.append(value)
        params.extend([min(max(limit, 1), 500), max(offset, 0)])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self.db.fetch_all(
            f"""SELECT * FROM policy_decisions {where}
                ORDER BY created_at DESC LIMIT ${len(params) - 1} OFFSET ${len(params)}""",
            tuple(params),
        )
        return [self._decision_dict(row) for row in rows]

    @staticmethod
    def _matches(
        row,
        *,
        resource_id: str,
        risk_level: RiskLevel,
        actor_roles: set[str],
        attributes: dict[str, Any],
    ) -> bool:
        if not fnmatch.fnmatchcase(resource_id, row["resource_pattern"]):
            return False
        risk_levels = set(json.loads(row["risk_levels"] or "[]"))
        if risk_levels and str(risk_level) not in risk_levels:
            return False
        required_roles = set(json.loads(row["actor_roles"] or "[]"))
        if required_roles and not actor_roles.intersection(required_roles):
            return False
        conditions = json.loads(row["conditions"] or "{}")
        required_tags = set(conditions.get("risk_tags_any", []))
        actual_tags = set(attributes.get("risk_tags", []))
        if required_tags and not required_tags.intersection(actual_tags):
            return False
        return True

    @staticmethod
    def _rule_dict(row) -> dict:
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        for key in ("risk_levels", "actor_roles", "conditions"):
            result[key] = json.loads(result[key]) if result[key] else ([] if key != "conditions" else {})
        return result

    @staticmethod
    def _decision_dict(row) -> dict:
        result = dict(row)
        result["context_data"] = (
            json.loads(result["context_data"]) if result["context_data"] else {}
        )
        return result

    def _now(self):
        return datetime.now(timezone.utc).replace(tzinfo=None)
