import pytest

from harness.evaluation.service import EvaluationService
from harness.observability.audit import AuditService
from harness.observability.context import ExecutionContext, reset_execution_context, set_execution_context
from harness.security.models import ActorContext, ActorType


@pytest.fixture
async def evaluation_service(db):
    actor = ActorContext(
        actor_id="operator-1",
        actor_type=ActorType.USER,
        tenant_id="default",
        roles=frozenset({"operator"}),
        permissions=frozenset({"*"}),
    )
    token = set_execution_context(ExecutionContext(trace_id="eval-trace", span_id="root", actor=actor))
    yield EvaluationService(db, AuditService(db))
    reset_execution_context(token)


async def test_regression_run_uses_immutable_suite_snapshot(evaluation_service):
    suite = await evaluation_service.create_suite(
        name="release-regression",
        category="regression",
        cases=[{"id": "login"}, {"id": "deploy"}],
        pass_threshold=1.0,
    )
    run = await evaluation_service.start_run(suite["id"])

    completed = await evaluation_service.record_results(
        run["id"],
        [
            {"case_id": "login", "status": "passed", "score": 1.0},
            {"case_id": "deploy", "status": "failed", "score": 0.0},
        ],
    )

    assert completed["status"] == "failed"
    assert completed["summary"] == {
        "total": 2,
        "passed": 1,
        "pass_rate": 0.5,
        "passed_threshold": False,
    }
