"""Risk policy administration and decision query routes."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from harness.models.policy import PolicyRuleInput
from harness.observability.audit import record_audit
from harness.security.dependencies import require_permission
from harness.security.models import ActorContext

router = APIRouter()


@router.get("/rules")
async def list_policy_rules(
    request: Request,
    _: ActorContext = Depends(require_permission("policy:read")),
):
    rules = await request.app.state.policy_engine.list_rules()
    return {"rules": rules, "count": len(rules)}


@router.post("/rules")
async def create_policy_rule(
    body: dict,
    request: Request,
    _: ActorContext = Depends(require_permission("policy:manage")),
):
    try:
        rule = await request.app.state.policy_engine.upsert_rule(
            PolicyRuleInput.model_validate(body)
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await record_audit(
        "policy.rule_saved",
        resource_type="policy_rule",
        resource_id=rule["id"],
        input_data=rule,
    )
    return rule


@router.put("/rules/{rule_id}")
async def update_policy_rule(
    rule_id: str,
    body: dict,
    request: Request,
    _: ActorContext = Depends(require_permission("policy:manage")),
):
    if await request.app.state.policy_engine.get_rule(rule_id) is None:
        raise HTTPException(status_code=404, detail="Policy rule not found")
    try:
        rule = await request.app.state.policy_engine.upsert_rule(
            PolicyRuleInput.model_validate(body),
            rule_id=rule_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await record_audit(
        "policy.rule_saved",
        resource_type="policy_rule",
        resource_id=rule_id,
        input_data=rule,
    )
    return rule


@router.get("/decisions")
async def list_policy_decisions(
    request: Request,
    trace_id: str | None = None,
    resource_id: str | None = None,
    decision: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: ActorContext = Depends(require_permission("policy:read")),
):
    decisions = await request.app.state.policy_engine.list_decisions(
        trace_id=trace_id,
        resource_id=resource_id,
        decision=decision,
        limit=limit,
        offset=offset,
    )
    return {"decisions": decisions, "count": len(decisions)}
