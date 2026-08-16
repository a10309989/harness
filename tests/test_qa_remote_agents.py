import json
from types import SimpleNamespace

import pytest

from harness.a2a.example_agents import QAConversationRemoteAgent, QuestionClassifierRemoteAgent, create_example_agents
from harness.a2a.server import create_a2a_agent_app
from harness.models.a2a import A2AInvokeParams
from harness.models.agent_contracts import ClassificationResult, QAResult, QuestionIntent


async def test_question_classifier_routes_general_qa():
    result = await QuestionClassifierRemoteAgent().invoke(
        A2AInvokeParams(
            agent_id="remote.question_classifier",
            message="Temporal 和 A2A 在当前架构里分别负责什么？",
            session_id="session-1",
            input_data={"threshold": 0.72},
        )
    )

    classification = ClassificationResult.model_validate_json(result.message)

    assert classification.intent == QuestionIntent.GENERAL_QA
    assert classification.target_agent_id == "remote.qa_conversation"
    assert classification.confidence >= 0.72


async def test_question_classifier_low_confidence_returns_unknown():
    result = await QuestionClassifierRemoteAgent().invoke(
        A2AInvokeParams(
            agent_id="remote.question_classifier",
            message="嗯",
            session_id="session-1",
            input_data={"threshold": 0.72},
        )
    )

    classification = ClassificationResult.model_validate_json(result.message)

    assert classification.intent == QuestionIntent.UNKNOWN
    assert classification.target_agent_id == "master"


async def test_qa_agent_returns_governed_operations_plan():
    result = await QAConversationRemoteAgent().invoke(
        A2AInvokeParams(
            agent_id="remote.qa_conversation",
            message="帮我看看 Redis DLQ 和 OTel 状态",
            session_id="session-1",
            input_data={"operations_allowed": True},
        )
    )

    qa = QAResult.model_validate_json(result.message)

    assert qa.answer_type == "operations_guidance"
    assert qa.operations[0]["mode"] == "governed"
    assert result.metadata["temporal_required"] is False


async def test_example_remote_service_exposes_classifier_and_qa_cards():
    app = create_a2a_agent_app(create_example_agents())
    cards = app.state.agent_registry.cards()
    ids = {card.agent_id for card in cards}

    assert "remote.question_classifier" in ids
    assert "remote.qa_conversation" in ids


def test_qa_result_contract_serializes_sources():
    payload = QAResult(
        answer="ok",
        confidence=0.8,
        sources=[{"type": "document", "ref": "docs/qa-agent-requirements.md"}],
    )

    assert json.loads(payload.model_dump_json())["sources"][0]["ref"].endswith(".md")
