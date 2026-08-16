"""Session API routes connecting messages to the agent pipeline."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from harness.api.deps import (
    get_agent_event_service,
    get_agent_registry,
    get_event_bus,
    get_session_manager,
    get_workflow_service,
)
from harness.core.events import Event, EventBus
from harness.core.registry import AgentRegistry
from harness.core.session import SessionManager
from harness.models.workflow import ExecutionSuspended
from harness.models.agent_contracts import MindMapNode
from harness.observability.agent_events import AgentEventService
from harness.observability.audit import record_audit
from harness.observability.context import child_span, get_execution_context
from harness.security.dependencies import require_permission
from harness.security.models import ActorContext
from harness.temporal.models import ApprovalWorkflowInput
from harness.utils.id_gen import generate_id
from harness.workflow.service import WorkflowService

logger = logging.getLogger(__name__)
router = APIRouter()


class RequirementReviewRequest(BaseModel):
    request: str


class RequirementReviewRevision(BaseModel):
    notes: str


class RequirementReviewMindMapUpdate(BaseModel):
    mind_map: dict


def _review_result(outcome, review: dict) -> dict:
    """Serialize a synchronous requirements-review response for the chat UI."""
    return {
        "review": review,
        "message": outcome.message,
        "status": outcome.status,
        "artifacts": [artifact.model_dump(mode="json") for artifact in outcome.artifacts],
        "artifact_cards": [card.model_dump(mode="json") for card in outcome.artifact_cards],
        "metadata": outcome.metadata,
    }


async def _run_requirements_review(
    *,
    registry: AgentRegistry,
    session_id: str,
    request_text: str,
    notes: list[str],
    confirmed: bool,
):
    try:
        agent = registry.get_agent("requirements_analyst")
    except KeyError as exc:
        raise HTTPException(status_code=503, detail="Requirements Analysis Agent is unavailable") from exc
    stage = "confirmed" if confirmed else "draft"
    notes_block = "\n\n".join(notes)
    message = (
        f"[requirements-review-{stage}]\n{request_text}\n"
        "请基于知识库需求文档完成需求分析。"
    )
    if notes_block:
        message += f"\n[user-review-notes]\n{notes_block}"
    if confirmed:
        message += "\n需求已由用户最终确认，请生成测试策略与可追溯产物。"
    else:
        message += "\n请仅生成需求确认思维导图草案，不要生成测试策略。"
    with child_span(session_id=session_id):
        await record_audit(
            "agent.invocation.started",
            resource_type="agent",
            resource_id="requirements_analyst",
            decision="running",
            metadata={"stage": "requirements_review", "review_stage": stage},
        )
        try:
            outcome = await agent.process_structured(message, session_id)
        except Exception as exc:
            await record_audit(
                "agent.invocation.failed",
                resource_type="agent",
                resource_id="requirements_analyst",
                decision="failed",
                reason=str(exc),
                metadata={"stage": "requirements_review", "review_stage": stage},
            )
            raise
        await record_audit(
            "agent.invocation.completed",
            resource_type="agent",
            resource_id="requirements_analyst",
            decision=outcome.status,
            metadata={
                "stage": "requirements_review",
                "review_stage": stage,
                "artifact_count": len(outcome.artifacts),
            },
        )
        return outcome


@router.post("")
async def create_session(
    session_mgr: SessionManager = Depends(get_session_manager),
    actor: ActorContext = Depends(require_permission("session:create")),
):
    session = session_mgr.create_session(
        user_id=actor.actor_id,
        tenant_id=actor.tenant_id,
    )
    await record_audit(
        "session.created",
        resource_type="session",
        resource_id=session.id,
        output_data={"state": session.state},
    )
    return {
        "session_id": session.id,
        "state": session.state,
        "created_at": session.created_at.isoformat(),
    }


@router.get("")
async def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    session_mgr: SessionManager = Depends(get_session_manager),
    actor: ActorContext = Depends(require_permission("session:read")),
):
    sessions = await session_mgr.list_sessions(
        tenant_id=actor.tenant_id,
        user_id=actor.actor_id,
        limit=limit,
    )
    return {"sessions": sessions, "count": len(sessions)}


@router.post("/{session_id}/requirements-review")
async def create_requirements_review(
    session_id: str,
    body: RequirementReviewRequest,
    session_mgr: SessionManager = Depends(get_session_manager),
    registry: AgentRegistry = Depends(get_agent_registry),
    actor: ActorContext = Depends(require_permission("session:message")),
):
    """Create the first reviewable requirements mind-map draft synchronously."""
    session = await session_mgr.reload_session(session_id)
    if session is None or session.tenant_id != actor.tenant_id or session.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Session not found")
    request_text = body.request.strip()
    if not request_text:
        raise HTTPException(status_code=400, detail="Requirement request is required")

    review = {
        "review_id": generate_id(),
        "status": "draft",
        "source_request": request_text,
        "notes": [],
        "revision": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await session_mgr.add_turn_durable(session_id, "user", request_text)
    outcome = await _run_requirements_review(
        registry=registry,
        session_id=session_id,
        request_text=request_text,
        notes=[],
        confirmed=False,
    )
    review.update(
        {
            "latest_mind_map": (outcome.metadata or {}).get("mind_map"),
            "latest_artifact_cards": [card.model_dump(mode="json") for card in outcome.artifact_cards],
        }
    )
    await session_mgr.update_context(session_id, {"requirements_review": review})
    await session_mgr.add_turn_durable(
        session_id,
        "assistant",
        outcome.message,
        {
            "requirements_review": review,
            "requirements_review_anchor": True,
            "artifact_cards": review["latest_artifact_cards"],
        },
    )
    await record_audit(
        "requirements_review.draft_created",
        resource_type="session",
        resource_id=session_id,
        metadata={"review_id": review["review_id"], "agent_id": "requirements_analyst"},
    )
    return _review_result(outcome, review)


@router.post("/{session_id}/requirements-review/{review_id}/revise")
async def revise_requirements_review(
    session_id: str,
    review_id: str,
    body: RequirementReviewRevision,
    session_mgr: SessionManager = Depends(get_session_manager),
    registry: AgentRegistry = Depends(get_agent_registry),
    actor: ActorContext = Depends(require_permission("session:message")),
):
    """Apply user additions/corrections and render a replacement mind-map draft."""
    session = await session_mgr.reload_session(session_id)
    review = (session.context_data or {}).get("requirements_review") if session else None
    if session is None or session.tenant_id != actor.tenant_id or not isinstance(review, dict) or review.get("review_id") != review_id:
        raise HTTPException(status_code=404, detail="Requirements review not found")
    if review.get("status") == "confirmed":
        raise HTTPException(status_code=409, detail="Confirmed requirements review cannot be revised")
    notes = body.notes.strip()
    if not notes:
        raise HTTPException(status_code=400, detail="Revision notes are required")

    all_notes = [*review.get("notes", []), notes]
    outcome = await _run_requirements_review(
        registry=registry,
        session_id=session_id,
        request_text=review["source_request"],
        notes=all_notes,
        confirmed=False,
    )
    review.update(
        {
            "status": "draft",
            "notes": all_notes,
            "revision": int(review.get("revision", 1)) + 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "latest_mind_map": (outcome.metadata or {}).get("mind_map"),
            "latest_artifact_cards": [card.model_dump(mode="json") for card in outcome.artifact_cards],
        }
    )
    await session_mgr.update_context(session_id, {"requirements_review": review})
    await session_mgr.add_turn_durable(session_id, "user", f"需求补充/修改：{notes}")
    await session_mgr.replace_requirements_review_metadata(
        session_id,
        review_id,
        review,
        anchor_latest=False,
    )
    await session_mgr.add_turn_durable(
        session_id, "assistant", outcome.message,
        {
            "requirements_review": review,
            "requirements_review_anchor": True,
            "artifact_cards": review["latest_artifact_cards"],
        },
    )
    await record_audit("requirements_review.revised", resource_type="session", resource_id=session_id, metadata={"review_id": review_id, "revision": review["revision"]})
    return _review_result(outcome, review)


@router.post("/{session_id}/requirements-review/{review_id}/confirm")
async def confirm_requirements_review(
    session_id: str,
    review_id: str,
    session_mgr: SessionManager = Depends(get_session_manager),
    registry: AgentRegistry = Depends(get_agent_registry),
    actor: ActorContext = Depends(require_permission("session:message")),
):
    """Final confirmation gate: only this operation generates the test strategy."""
    session = await session_mgr.get_session_or_load(session_id)
    review = (session.context_data or {}).get("requirements_review") if session else None
    if session is None or session.tenant_id != actor.tenant_id or not isinstance(review, dict) or review.get("review_id") != review_id:
        raise HTTPException(status_code=404, detail="Requirements review not found")
    if review.get("status") == "confirmed":
        raise HTTPException(status_code=409, detail="Requirements review already confirmed")

    outcome = await _run_requirements_review(
        registry=registry,
        session_id=session_id,
        request_text=review["source_request"],
        notes=list(review.get("notes", [])),
        confirmed=True,
    )
    review.update(
        {
            "status": "confirmed",
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
            "strategy_artifact_cards": [card.model_dump(mode="json") for card in outcome.artifact_cards],
        }
    )
    await session_mgr.update_context(session_id, {"requirements_review": review})
    await session_mgr.replace_requirements_review_metadata(
        session_id,
        review_id,
        review,
        anchor_latest=True,
    )
    await session_mgr.add_turn_durable(
        session_id,
        "assistant",
        outcome.message,
        {
            "requirements_review": review,
            "requirements_review_anchor": False,
            "artifact_cards": review["strategy_artifact_cards"],
        },
    )
    await record_audit("requirements_review.confirmed", resource_type="session", resource_id=session_id, metadata={"review_id": review_id, "agent_id": "requirements_analyst"})
    return _review_result(outcome, review)


@router.put("/{session_id}/requirements-review/{review_id}/mind-map")
async def update_requirements_review_mind_map(
    session_id: str,
    review_id: str,
    body: RequirementReviewMindMapUpdate,
    session_mgr: SessionManager = Depends(get_session_manager),
    actor: ActorContext = Depends(require_permission("session:message")),
):
    """Persist user edits to the current requirements mind-map draft."""
    session = await session_mgr.get_session_or_load(session_id)
    review = (session.context_data or {}).get("requirements_review") if session else None
    if session is None or session.tenant_id != actor.tenant_id or not isinstance(review, dict) or review.get("review_id") != review_id:
        raise HTTPException(status_code=404, detail="Requirements review not found")
    if review.get("status") == "confirmed":
        raise HTTPException(status_code=409, detail="Confirmed requirements review cannot be edited")
    try:
        mind_map = MindMapNode.model_validate(body.mind_map).model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid mind-map structure") from exc
    review.update(
        {
            "latest_mind_map": mind_map,
            "manual_edit_count": int(review.get("manual_edit_count", 0)) + 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    await session_mgr.update_context(session_id, {"requirements_review": review})
    await session_mgr.update_latest_turn_metadata(
        session_id,
        "assistant",
        {"requirements_review": review},
    )
    await record_audit("requirements_review.mind_map_edited", resource_type="session", resource_id=session_id, metadata={"review_id": review_id})
    return {"review": review}


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    session_mgr: SessionManager = Depends(get_session_manager),
    actor: ActorContext = Depends(require_permission("session:read")),
):
    session = await session_mgr.reload_session(session_id)
    if session is None or session.tenant_id != actor.tenant_id or session.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session.id,
        "state": session.state,
        "turns": len(session.conversation),
        "conversation": [
            {
                "role": turn.role,
                "content": turn.content,
                "timestamp": turn.timestamp.isoformat(),
                "metadata": turn.metadata,
            }
            for turn in session.conversation
        ],
    }


@router.get("/{session_id}/messages")
async def get_messages(
    session_id: str,
    session_mgr: SessionManager = Depends(get_session_manager),
    actor: ActorContext = Depends(require_permission("session:read")),
):
    session = await session_mgr.reload_session(session_id)
    if session is None or session.tenant_id != actor.tenant_id or session.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session.id,
        "messages": [
            {"role": turn.role, "content": turn.content, "metadata": turn.metadata}
            for turn in session.conversation
        ],
    }


class TurnRequest(BaseModel):
    role: str
    content: str
    metadata: dict = {}


@router.post("/{session_id}/turns")
async def append_turn(
    session_id: str,
    body: TurnRequest,
    session_mgr: SessionManager = Depends(get_session_manager),
    actor: ActorContext = Depends(require_permission("session:read")),
):
    """Persist a conversation turn (used by pipeline flow messages so the dialog
    survives a page refresh — regular chat messages go through send_message)."""
    session = await session_mgr.get_session_or_load(session_id)
    if session is None or session.tenant_id != actor.tenant_id or session.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Session not found")
    session_mgr.add_turn(session_id, body.role, body.content, body.metadata)
    return {"ok": True, "session_id": session_id}


@router.get("/{session_id}/events")
async def get_session_events(
    session_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    session_mgr: SessionManager = Depends(get_session_manager),
    events: AgentEventService = Depends(get_agent_event_service),
    actor: ActorContext = Depends(require_permission("session:read")),
):
    session = await session_mgr.get_session_or_load(session_id)
    if session is None or session.tenant_id != actor.tenant_id or session.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Session not found")
    timeline = await events.list_by_session(session_id, limit=limit)
    return {"session_id": session_id, "events": timeline, "count": len(timeline)}


@router.post("/{session_id}/message-async")
async def send_message_async(
    session_id: str,
    body: dict,
    request: Request,
    session_mgr: SessionManager = Depends(get_session_manager),
    registry: AgentRegistry = Depends(get_agent_registry),
    actor: ActorContext = Depends(require_permission("session:message")),
):
    """Stateless submission: master classifies + plans, returns a durable run_id.

    A ``WorkflowDriver`` (multi-replica safe) advances the DAG in the
    background; poll ``GET /workflows/{run_id}`` for status.
    """
    session = session_mgr.get_session(session_id)
    if session is None or session.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=404, detail="Session not found")
    message = body.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    session_mgr.activate_session(session_id)
    session_mgr.add_turn(session_id, "user", message)
    try:
        master = registry.get_master_agent()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Master Agent not initialized")
    result = await master.submit_plan(message, session_id)
    return {
        "session_id": session_id,
        "run_id": result.get("run_id"),
        "status": result.get("status"),
        "message": result.get("message"),
        "plan": master.get_state("last_plan"),
    }


@router.post("/{session_id}/message")
async def send_message(
    session_id: str,
    body: dict,
    request: Request,
    session_mgr: SessionManager = Depends(get_session_manager),
    registry: AgentRegistry = Depends(get_agent_registry),
    event_bus: EventBus = Depends(get_event_bus),
    workflow_service: WorkflowService = Depends(get_workflow_service),
    actor: ActorContext = Depends(require_permission("session:message")),
):
    session = session_mgr.get_session(session_id)
    if session is None or session.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=404, detail="Session not found")

    message = body.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    session_mgr.activate_session(session_id)
    session_mgr.add_turn(session_id, "user", message)

    requested_agent_id = str(body.get("agent_id") or "").strip()
    try:
        target_agent = (
            registry.get_agent(requested_agent_id)
            if requested_agent_id
            else registry.get_master_agent()
        )
    except (KeyError, RuntimeError):
        raise HTTPException(
            status_code=400 if requested_agent_id else 503,
            detail=(
                f"Requested agent '{requested_agent_id}' is unavailable."
                if requested_agent_id
                else "Master Agent not initialized. Check server configuration."
            ),
        )

    workflow_id = await workflow_service.create_run(
        session_id=session_id,
        input_data={"message": message},
    )

    temporal_client = getattr(request.app.state, "temporal_approval_client", None)
    if (
        request.app.state.settings.temporal_authoritative_execution
        and temporal_client is not None
    ):
        from harness.temporal.models import AgentWorkflowInput

        await temporal_client.start_agent(
            AgentWorkflowInput(
                workflow_id=workflow_id,
                agent_id=target_agent.agent_id,
                message=message,
                session_id=session_id,
                trace_id=get_execution_context().trace_id,
            )
        )
        await record_audit(
            "session.message_queued",
            resource_type="session",
            resource_id=session_id,
            metadata={
                "workflow_id": workflow_id,
                "execution_engine": "temporal",
                "target_agent": target_agent.agent_id,
            },
        )
        return JSONResponse(
            status_code=202,
            content={
                "session_id": session_id,
                "workflow_id": workflow_id,
                "state": "running",
                "message": "Agent execution was submitted to Temporal.",
            },
        )

    with child_span(session_id=session_id, workflow_id=workflow_id):
        await record_audit(
            "session.message_received",
            resource_type="session",
            resource_id=session_id,
            input_data={"message": message},
            metadata={"message_length": len(message)},
        )
        try:
            if hasattr(target_agent, "process_structured"):
                outcome = await target_agent.process_structured(message, session_id)
            else:
                response = await target_agent.process(message, session_id)
                from harness.models.artifact import AgentOutcome

                outcome = AgentOutcome(message=response)
            await workflow_service.complete(
                workflow_id,
                {
                    "message": outcome.message,
                    "artifacts": [artifact.model_dump(mode="json") for artifact in outcome.artifacts],
                    "metadata": outcome.metadata,
                },
            )
            await event_bus.publish(
                Event(
                    event_type="message.processed",
                    payload={"session_id": session_id, "message_length": len(message)},
                    session_id=session_id,
                )
            )
            session_mgr.add_turn(session_id, "assistant", outcome.message)
            await record_audit(
                "session.message_processed",
                resource_type="session",
                resource_id=session_id,
                output_data={"message": outcome.message},
                metadata={
                    "response_length": len(outcome.message),
                    "artifact_count": len(outcome.artifacts),
                },
            )
            return {
                "session_id": session_id,
                "workflow_id": workflow_id,
                "message": outcome.message,
                "status": outcome.status,
                "artifacts": [artifact.model_dump(mode="json") for artifact in outcome.artifacts],
                "artifact_cards": [card.model_dump(mode="json") for card in outcome.artifact_cards],
                "metadata": outcome.metadata,
                "agent_pipeline": {
                    "master_agent": master.agent_name,
                    "intent": _get_last_intent(master),
                    "plan": master.get_state("last_plan"),
                    "context_turns": master.turn_count,
                },
            }
        except ExecutionSuspended as exc:
            temporal_client = getattr(request.app.state, "temporal_approval_client", None)
            if temporal_client is not None:
                try:
                    await temporal_client.start(
                        ApprovalWorkflowInput(
                            workflow_id=workflow_id,
                            human_task_id=exc.info.human_task_id,
                            trace_id=get_execution_context().trace_id,
                            session_id=session_id,
                            resume_with_temporal=request.app.state.settings.temporal_authoritative_execution,
                        )
                    )
                except Exception:
                    logger.exception(
                        "Unable to mirror approval wait to Temporal for workflow %s",
                        workflow_id,
                    )
            session_mgr.add_turn(
                session_id,
                "system",
                f"Workflow suspended for approval task {exc.info.human_task_id}",
            )
            return JSONResponse(
                status_code=202,
                content={
                    "session_id": session_id,
                    "workflow_id": workflow_id,
                    "state": "suspended",
                    "message": "Execution is waiting for human approval.",
                    "suspension": exc.info.model_dump(),
                },
            )
        except Exception as exc:
            logger.error("Agent processing error: %s", exc)
            await workflow_service.fail(workflow_id, str(exc))
            error_msg = f"Processing error: {exc}"
            session_mgr.add_turn(session_id, "system", error_msg)
            await record_audit(
                "session.message_failed",
                resource_type="session",
                resource_id=session_id,
                decision="failure",
                reason=exc.__class__.__name__,
            )
            return {
                "session_id": session_id,
                "message": error_msg,
                "error": str(exc),
            }


def _get_last_intent(master) -> dict | None:
    state = master.get_state("last_intent")
    if state:
        return state
    for turn in reversed(master.get_context_window()):
        if turn.metadata and "classification" in turn.metadata:
            return turn.metadata["classification"]
    return None


@router.post("/{session_id}/archive")
async def archive_session(
    session_id: str,
    session_mgr: SessionManager = Depends(get_session_manager),
    actor: ActorContext = Depends(require_permission("session:message")),
):
    try:
        session = session_mgr.get_session(session_id)
        if session is None or session.tenant_id != actor.tenant_id:
            raise KeyError(session_id)
        session = session_mgr.end_session(session_id)
        await record_audit(
            "session.completed",
            resource_type="session",
            resource_id=session_id,
            output_data={"state": session.state},
        )
        return {"session_id": session.id, "state": session.state}
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    session_mgr: SessionManager = Depends(get_session_manager),
    actor: ActorContext = Depends(require_permission("session:message")),
):
    try:
        session = await session_mgr.get_session_or_load(session_id)
        if session is None or session.tenant_id != actor.tenant_id:
            raise KeyError(session_id)
        session = await session_mgr.delete_session(session_id, actor.actor_id)
        await record_audit(
            "session.deleted",
            resource_type="session",
            resource_id=session_id,
            output_data={"deleted_at": session.deleted_at.isoformat() if session.deleted_at else None},
        )
        return {
            "session_id": session.id,
            "deleted_at": session.deleted_at.isoformat() if session.deleted_at else None,
        }
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
