"""Execution API routes backed by Runner Manager and immutable artifacts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from harness.execution.runner_manager import RunnerClient
from harness.models.artifact import ArtifactDisplayRef, ArtifactDraft, ArtifactType
from harness.models.execution import ExecutionResult, ExecutionStatus
from harness.observability.audit import record_audit
from harness.observability.context import new_id
from harness.security.dependencies import require_permission
from harness.security.models import ActorContext

router = APIRouter()


@router.post("")
async def trigger_execution(
    body: dict,
    request: Request,
    actor: ActorContext = Depends(require_permission("execution:invoke")),
):
    """Trigger a test execution from a test_script artifact version."""
    version_id = (
        body.get("artifact_version_id")
        or body.get("version_id")
        or body.get("script_artifact_version_id")
    )
    if not version_id:
        raise HTTPException(status_code=400, detail="artifact_version_id is required")
    timeout_seconds = int(body.get("timeout_seconds", 300))
    return await _execute_script_artifact(
        request,
        version_id,
        timeout_seconds,
        session_id=body.get("session_id"),
        actor=actor,
    )


@router.post("/artifacts/{version_id}/execute")
async def execute_script_artifact(
    version_id: str,
    body: dict,
    request: Request,
    actor: ActorContext = Depends(require_permission("execution:invoke")),
):
    timeout_seconds = int(body.get("timeout_seconds", 300))
    return await _execute_script_artifact(
        request,
        version_id,
        timeout_seconds,
        session_id=body.get("session_id"),
        actor=actor,
    )


@router.get("")
async def list_executions(request: Request, _: ActorContext = Depends(require_permission("execution:read"))):
    """List execution report artifacts."""
    artifacts = await request.app.state.artifact_service.list(
        artifact_type=str(ArtifactType.EXECUTION_REPORT),
        status="active",
        limit=100,
        offset=0,
    )
    return {"executions": artifacts, "count": len(artifacts)}


@router.get("/{execution_id}")
async def get_execution(
    execution_id: str,
    request: Request,
    _: ActorContext = Depends(require_permission("execution:read")),
):
    """Get execution report artifact by artifact id."""
    artifact = await request.app.state.artifact_service.get(execution_id)
    if artifact is None or artifact.get("artifact_type") != str(ArtifactType.EXECUTION_REPORT):
        raise HTTPException(status_code=404, detail="Execution report not found")
    return artifact


@router.get("/{execution_id}/diagnosis")
async def get_diagnosis(
    execution_id: str,
    _: ActorContext = Depends(require_permission("execution:read")),
):
    """Get AI diagnosis for a failed execution."""
    return {
        "execution_id": execution_id,
        "root_cause": "Diagnosis not yet available",
        "suggested_fixes": [],
    }


async def _execute_script_artifact(
    request: Request,
    version_id: str,
    timeout_seconds: int,
    *,
    session_id: str | None = None,
    actor: ActorContext,
) -> dict[str, Any]:
    artifact_service = request.app.state.artifact_service
    settings = request.app.state.settings
    try:
        version, raw_content = await artifact_service.read_version(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    artifact = await artifact_service.get(version["artifact_id"])
    if artifact is None:
        raise HTTPException(status_code=404, detail="Script artifact not found")
    if artifact.get("artifact_type") != str(ArtifactType.TEST_SCRIPT):
        raise HTTPException(status_code=400, detail="Only test_script artifacts can be executed")

    source_code = raw_content.decode("utf-8", errors="replace")
    script_metadata = version.get("metadata") or {}
    runner_environment = _runner_environment(script_metadata, settings)
    execution_request_id = new_id()
    await record_audit(
        "execution.requested",
        resource_type="artifact",
        resource_id=version_id,
        metadata={
            "execution_request_id": execution_request_id,
            "runner_manager_url": settings.runner_manager_url,
            "timeout_seconds": timeout_seconds,
            "test_kind": script_metadata.get("test_kind"),
        },
    )

    client = RunnerClient(
        endpoint=settings.runner_manager_url,
        token=settings.runner_rpc_token,
        timeout_seconds=timeout_seconds + 30,
    )
    try:
        result = await client.execute(
            script_id=version["artifact_id"],
            test_case_id=(version.get("metadata") or {}).get("source_artifact_version_id", version_id),
            source_code=source_code,
            execution_request_id=execution_request_id,
            timeout_seconds=timeout_seconds,
            environment=runner_environment,
        )
    except Exception as exc:
        now = datetime.now(timezone.utc)
        result = ExecutionResult(
            id=new_id(),
            execution_request_id=execution_request_id,
            script_id=version["artifact_id"],
            test_case_id=(version.get("metadata") or {}).get("source_artifact_version_id", version_id),
            status=ExecutionStatus.ERROR,
            start_time=now,
            end_time=now,
            duration_seconds=0,
            stderr=f"Runner Manager execution failed: {exc}",
        )

    report_payload = {
        "schema_version": "1.0",
        "execution_request_id": execution_request_id,
        "script_artifact_id": version["artifact_id"],
        "script_artifact_version_id": version_id,
        "runner_manager_url": settings.runner_manager_url,
        "result": result.model_dump(mode="json"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    report_ref = await artifact_service.create(
        ArtifactDraft(
            artifact_type=ArtifactType.EXECUTION_REPORT,
            name=f"执行报告 - {execution_request_id}",
            content=json.dumps(report_payload, ensure_ascii=False, indent=2),
            media_type="application/json",
            metadata={
                "execution_request_id": execution_request_id,
                "source_artifact_version_id": version_id,
                "execution_status": str(result.status),
                "runner_manager_url": settings.runner_manager_url,
            },
        )
    )
    await artifact_service.link(
        version_id,
        report_ref.version_id,
        "generates_execution_report",
        {
            "execution_request_id": execution_request_id,
            "execution_status": str(result.status),
        },
    )
    await record_audit(
        "execution.completed",
        resource_type="execution_report",
        resource_id=report_ref.artifact_id,
        decision=str(result.status),
        metadata={
            "execution_request_id": execution_request_id,
            "script_artifact_version_id": version_id,
            "report_artifact_version_id": report_ref.version_id,
        },
    )
    card = ArtifactDisplayRef(
        **report_ref.model_dump(mode="json"),
        artifact_type=str(ArtifactType.EXECUTION_REPORT),
        name="执行报告",
        actions=["view", "download"],
        metadata={
            "execution_request_id": execution_request_id,
            "execution_status": str(result.status),
            "source_artifact_version_id": version_id,
        },
    )
    response = {
        "execution_request_id": execution_request_id,
        "status": str(result.status),
        "result": result.model_dump(mode="json"),
        "artifacts": [report_ref.model_dump(mode="json")],
        "artifact_cards": [card.model_dump(mode="json")],
        "report_artifact_version_id": report_ref.version_id,
    }
    if session_id:
        session = request.app.state.session_manager.get_session(session_id)
        if session is None or session.tenant_id != actor.tenant_id or session.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Session not found")
        request.app.state.session_manager.add_turn(
            session_id,
            "assistant",
            _format_execution_message(response),
            {
                "artifacts": response["artifacts"],
                "artifact_cards": response["artifact_cards"],
                "execution_result": response["result"],
            },
        )
    return response


def _format_execution_message(response: dict[str, Any]) -> str:
    return "\n".join(
        [
            "✅ 测试执行已完成并生成执行报告。",
            "",
            f"- 执行状态：{response['status']}",
            f"- execution_request_id：{response['execution_request_id']}",
            f"- 报告 version_id：`{response['report_artifact_version_id']}`",
        ]
    )


def _runner_environment(metadata: dict[str, Any], settings) -> dict[str, str]:
    if metadata.get("test_kind") != "ui":
        return {}
    ui_profile = metadata.get("ui_profile") if isinstance(metadata.get("ui_profile"), dict) else {}
    return {
        "HARNESS_UI_TARGET_URL": str(
            ui_profile.get("target_url")
            or getattr(settings, "ui_test_target_url", "http://host.docker.internal:5173")
        )
    }
