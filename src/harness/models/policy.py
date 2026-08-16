"""Risk policy models."""

from enum import StrEnum
from typing import Any

from harness.models.common import HarnessBaseModel


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PolicyEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    REQUIRE_TWO_PERSON_APPROVAL = "require_two_person_approval"


class PolicyRuleInput(HarnessBaseModel):
    name: str
    priority: int = 100
    enabled: bool = True
    effect: PolicyEffect
    resource_type: str = "tool"
    resource_pattern: str = "*"
    risk_levels: list[RiskLevel] = []
    actor_roles: list[str] = []
    conditions: dict[str, Any] = {}
    reason: str = ""


class PolicyDecision(HarnessBaseModel):
    id: str
    decision: PolicyEffect
    risk_level: RiskLevel
    resource_type: str
    resource_id: str
    matched_rule_id: str | None = None
    matched_rule_version_id: str | None = None
    rule_digest: str | None = None
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision == PolicyEffect.ALLOW
