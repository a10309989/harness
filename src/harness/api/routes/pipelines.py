"""Pipeline lifecycle routes: DAG planning, confirmation, execution, resume."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from harness.security.dependencies import require_permission
from harness.security.models import ActorContext

router = APIRouter()
logger = logging.getLogger(__name__)


class PlanRequest(BaseModel):
    session_id: str
    objective: str
    answers: dict[str, str] = {}


class RequirementsConfirmationRequest(BaseModel):
    strategy_artifact_version_id: str


class TestCaseDraftRevisionRequest(BaseModel):
    content: str


class TestCaseDraftRegenerationRequest(BaseModel):
    feedback: str


class ClassifyRequest(BaseModel):
    session_id: str
    message: str


async def _svc(request: Request):
    return request.app.state.pipeline_service


# Lifecycle stages a "run the full test lifecycle" request can span. A message
# that mentions >= 2 distinct stages is treated as a pipeline-worthy request —
# independent of the (noisier) intent classifier.
_LIFECYCLE_STAGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("requirements", ("需求", "需求分析", "requirement", "prd", "spec", "用户故事")),
    ("test_case", ("用例", "测试用例", "test case", "测试场景", "场景")),
    ("script", ("脚本", "自动化脚本", "pytest", "selenium", "playwright", "script")),
    ("execution", ("执行", "运行", "跑测试", "执行测试", "自动化测试", "run", "execute")),
    ("report", ("报告", "report", "覆盖率", "缺陷统计", "质量报告")),
)

_PIPELINE_INTENTS = {
    "requirements_analysis",
    "test_case_generation",
    "script_generation",
    "schedule_execution",
}


def _lifecycle_stages(message: str) -> list[str]:
    lowered = message.lower()
    return [
        stage
        for stage, keywords in _LIFECYCLE_STAGES
        if any(keyword in lowered or keyword in message for keyword in keywords)
    ]


@router.post("/classify")
async def classify_pipeline(
    body: ClassifyRequest,
    request: Request,
    _: ActorContext = Depends(require_permission("session:message")),
):
    """Decide whether a message should run the interactive pipeline, and if so
    return clarifying questions to ask the user before planning."""
    registry = request.app.state.agent_registry
    master = registry.get_master_agent()

    intent = None
    confidence = 0.0
    try:
        classification = await master.classifier_chain.classify(
            body.message, {"session_id": body.session_id}
        )
        intent_value = classification.intent if classification else None
        intent = getattr(intent_value, "value", intent_value) if intent_value else None
        confidence = classification.confidence if classification else 0.0
    except Exception as exc:  # pragma: no cover - defensive
        import logging

        logging.getLogger(__name__).exception("classify intent failed: %s", exc)
        intent = None

    stages = _lifecycle_stages(body.message)
    in_pipeline_intent = intent in _PIPELINE_INTENTS if intent else False
    needs_pipeline = in_pipeline_intent or len(stages) >= 2

    questions: list[dict] = []
    if needs_pipeline:
        questions = await _generate_clarifying_questions(master, body.message)

    return {
        "needs_pipeline": needs_pipeline,
        "intent": intent,
        "confidence": confidence,
        "stages": stages,
        "questions": questions,
    }


async def _generate_clarifying_questions(master, message: str) -> list[dict]:
    """Ask the LLM for 1-3 clarifying questions about a pipeline objective."""
    llm_call = getattr(master, "_llm_text_call", None)
    if llm_call is None:
        return []
    prompt = (
        "The user wants to run an automated test lifecycle (requirements -> test "
        "cases -> scripts -> execution -> report). Before planning, ask 2-3 concise "
        "clarifying questions to disambiguate the task.\n\n"
        f"User request: {message}\n\n"
        'Return ONLY a JSON array of objects: [{"id":"q1","question":"..."}] '
        "3 items max, each question one short sentence, in the user's language."
    )
    try:
        response = await llm_call(
            "You are a test-automation assistant that clarifies requirements before planning.",
            prompt,
        )
        content = getattr(response, "content", response) or ""
        cleaned = _extract_json(content)
        data = json.loads(cleaned)
        if isinstance(data, list):
            return [
                {"id": str(item.get("id", f"q{index + 1}")), "question": str(item.get("question", ""))}
                for index, item in enumerate(data)
                if item.get("question")
            ][:3]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return []


def _extract_json(text: str) -> str:
    """Extract a JSON value (object or array) from a model response.

    ``harness.core.agent._clean_json_response`` only handles single objects;
    the clarification prompt returns an array, so we extract the outermost
    ``[ ... ]`` / ``{ ... }`` span instead.
    """
    import re

    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start = cleaned.find(open_ch)
        end = cleaned.rfind(close_ch)
        if start >= 0 and end > start:
            return cleaned[start : end + 1]
    return cleaned


@router.post("/plan")
async def plan_pipeline(
    body: PlanRequest,
    request: Request,
    _: ActorContext = Depends(require_permission("session:message")),
):
    try:
        return await (await _svc(request)).plan_pipeline(
            session_id=body.session_id,
            objective=body.objective,
            answers=body.answers,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{pipeline_id}")
async def get_pipeline(
    pipeline_id: str,
    request: Request,
    _: ActorContext = Depends(require_permission("session:message")),
):
    try:
        return await (await _svc(request)).get_pipeline(pipeline_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{pipeline_id}/confirm")
async def confirm_pipeline(
    pipeline_id: str,
    request: Request,
    _: ActorContext = Depends(require_permission("session:message")),
):
    try:
        return await (await _svc(request)).confirm_pipeline(pipeline_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Pipeline confirmation failed: %s", pipeline_id)
        raise HTTPException(status_code=500, detail=f"Pipeline confirmation failed: {exc}") from exc


@router.post("/{pipeline_id}/resume")
async def resume_pipeline(
    pipeline_id: str,
    request: Request,
    _: ActorContext = Depends(require_permission("session:message")),
):
    try:
        return await (await _svc(request)).resume_pipeline(pipeline_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{pipeline_id}/retry")
async def retry_pipeline(
    pipeline_id: str,
    request: Request,
    _: ActorContext = Depends(require_permission("session:message")),
):
    """Retry a node interrupted before its output artifact was persisted."""
    try:
        return await (await _svc(request)).retry_pipeline(pipeline_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{pipeline_id}/requirements-confirmed")
async def complete_requirements_confirmation(
    pipeline_id: str,
    body: RequirementsConfirmationRequest,
    request: Request,
    _: ActorContext = Depends(require_permission("session:message")),
):
    try:
        return await (await _svc(request)).complete_requirements_confirmation(
            pipeline_id,
            body.strategy_artifact_version_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/{pipeline_id}/test-cases/draft")
async def revise_test_case_draft(
    pipeline_id: str,
    body: TestCaseDraftRevisionRequest,
    request: Request,
    _: ActorContext = Depends(require_permission("artifact:write")),
):
    try:
        return await (await _svc(request)).revise_test_case_draft(pipeline_id, body.content)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{pipeline_id}/test-cases/regenerate")
async def regenerate_test_case_draft(
    pipeline_id: str,
    body: TestCaseDraftRegenerationRequest,
    request: Request,
    _: ActorContext = Depends(require_permission("session:message")),
):
    try:
        return await (await _svc(request)).regenerate_test_case_draft(pipeline_id, body.feedback)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
