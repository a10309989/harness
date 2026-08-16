"""Example remote A2A agents for test-lifecycle automation."""

from __future__ import annotations

import json

from harness.a2a.requirements_analysis import HarnessRequirementsAnalyzer
from harness.a2a.server import RemoteA2AAgent, completed_result
from harness.models.a2a import A2AInvokeParams, A2AInvokeResult, AgentCard
from harness.models.agent import AgentCapability
from harness.models.agent_contracts import (
    ClassificationResult,
    DiagnosisFinding,
    DiagnosisReport,
    ExecutionReport,
    QAResult,
    QASourceRef,
    QuestionIntent,
    RequirementAnalysisPackage,
    RequirementSpec,
    ScriptBundle,
    ScriptFile,
    TestCase,
    TestCaseSet,
    TestStep,
)


class QuestionClassifierRemoteAgent(RemoteA2AAgent):
    def __init__(self) -> None:
        super().__init__(
            AgentCard(
                agent_id="remote.question_classifier",
                name="Remote Question Classification Agent",
                description="Classifies user questions and returns target Harness agent routing.",
                capabilities=[
                    AgentCapability(
                        name="question.classification",
                        description="Classify messages into QA or test-lifecycle intents",
                        input_schema={
                            "type": "object",
                            "required": ["message"],
                            "properties": {
                                "message": {"type": "string"},
                                "threshold": {"type": "number"},
                                "intent_mappings": {"type": "object"},
                            },
                        },
                        output_schema=ClassificationResult.model_json_schema(),
                    )
                ],
                output_schema=ClassificationResult.model_json_schema(),
                metadata={"contract": "ClassificationResult", "default_threshold": 0.72},
            )
        )

    async def invoke(self, params: A2AInvokeParams) -> A2AInvokeResult:
        mapping = params.input_data.get("intent_mappings") or {}
        result = self._classify(params.message, mapping)
        threshold = float(params.input_data.get("threshold", 0.72))
        if result.confidence < threshold:
            result = ClassificationResult(
                intent=QuestionIntent.UNKNOWN,
                confidence=result.confidence,
                reasoning=f"{result.reasoning} Confidence below threshold {threshold}.",
                target_agent_id=mapping.get("unknown", "master"),
                requires_workflow=False,
                risk_level="low",
            )
        return completed_result(
            params,
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
            metadata={"contract": "ClassificationResult", "threshold": threshold},
        )

    def _classify(self, message: str, mapping: dict) -> ClassificationResult:
        text = message.lower()
        rules = [
            (QuestionIntent.EXECUTION, ["执行", "运行", "run", "runner", "部署", "回滚"], "remote.execution", True, "medium"),
            (QuestionIntent.DIAGNOSIS, ["诊断", "日志", "报错", "失败", "error", "root cause", "trace"], "remote.diagnosis", True, "medium"),
            (QuestionIntent.SCRIPT, ["脚本", "script", "pytest", "playwright", "selenium", "代码"], "remote.scripts", True, "medium"),
            (QuestionIntent.TEST_CASE, ["用例", "test case", "测试点", "场景"], "remote.test_cases", True, "low"),
            (QuestionIntent.REQUIREMENTS, ["需求", "prd", "requirement", "验收标准", "拆解"], "remote.requirements", True, "low"),
        ]
        for intent, keywords, default_agent, requires_workflow, risk_level in rules:
            hits = [keyword for keyword in keywords if keyword in text]
            if hits:
                confidence = min(0.94, 0.78 + len(hits) * 0.05)
                return ClassificationResult(
                    intent=intent,
                    confidence=confidence,
                    reasoning=f"Matched keywords: {', '.join(hits)}",
                    target_agent_id=mapping.get(str(intent), mapping.get(intent.value, default_agent)),
                    requires_workflow=requires_workflow,
                    risk_level=risk_level,
                )
        if len(text.strip()) < 6:
            return ClassificationResult(
                intent=QuestionIntent.UNKNOWN,
                confidence=0.45,
                reasoning="Message is too short for confident routing.",
                target_agent_id=mapping.get("unknown", "master"),
            )
        return ClassificationResult(
            intent=QuestionIntent.GENERAL_QA,
            confidence=0.86,
            reasoning="No specialized test-lifecycle intent matched; route to QA conversation.",
            target_agent_id=mapping.get("general_qa", "remote.qa_conversation"),
        )


class QAConversationRemoteAgent(RemoteA2AAgent):
    def __init__(self) -> None:
        super().__init__(
            AgentCard(
                agent_id="remote.qa_conversation",
                name="Remote QA Conversation Agent",
                description="Answers free-form Harness platform and test automation questions.",
                capabilities=[
                    AgentCapability(
                        name="qa.conversation",
                        description="Answer general questions with optional knowledge and governed operations context",
                        input_schema={
                            "type": "object",
                            "required": ["message", "session_id"],
                            "properties": {
                                "message": {"type": "string"},
                                "session_id": {"type": "string"},
                                "context": {"type": "object"},
                                "sources": {"type": "array"},
                                "operations_allowed": {"type": "boolean"},
                            },
                        },
                        output_schema=QAResult.model_json_schema(),
                    )
                ],
                output_schema=QAResult.model_json_schema(),
                metadata={
                    "contract": "QAResult",
                    "synchronous": True,
                    "temporal_required": False,
                },
            )
        )

    async def invoke(self, params: A2AInvokeParams) -> A2AInvokeResult:
        result = self._answer(params)
        return completed_result(
            params,
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
            metadata={
                "contract": "QAResult",
                "persist_policy": "explicit_save_or_structured_report",
                "temporal_required": False,
            },
        )

    def _answer(self, params: A2AInvokeParams) -> QAResult:
        message = params.message.strip()
        text = message.lower()
        sources = [
            QASourceRef(type="document", ref=str(source))
            for source in params.input_data.get("sources", [])
        ]
        operations: list[dict] = []
        if any(token in text for token in ["ops", "运维", "状态", "dlq", "队列", "备份", "otel"]):
            if params.input_data.get("operations_allowed", False):
                operations.append(
                    {
                        "name": "ops.summary",
                        "mode": "governed",
                        "status": "planned",
                        "policy": "requires RBAC and audit before execution",
                    }
                )
                answer = (
                    "可以提供运维类辅助。当前回答会规划受治理的运维查询，"
                    "例如 Ops Summary、DLQ、Runner 队列、备份演练和 OTel 状态；"
                    "实际执行必须经过 RBAC、策略检查和审计。"
                )
            else:
                answer = (
                    "你问到了运维类问题。我可以解释排查路径，但当前请求未携带"
                    " operations_allowed=true，因此不会执行运维操作。"
                )
        elif any(token in text for token in ["temporal", "langgraph", "架构", "a2a", "agent"]):
            answer = (
                "从 Harness 架构看，Temporal 负责耐久工作流控制面，"
                "A2A 负责远程 Agent 互操作，QA 对话保持同步低延迟。"
                "如果问题进入需求、用例、脚本、执行或诊断领域，应由分类 Agent "
                "路由到对应专业 Agent。"
            )
        else:
            answer = (
                "这是一个通用问答请求。我会基于会话上下文和可用知识源回答；"
                "如果后续识别出测试生命周期任务，Master 会重新路由到专业 Agent。"
            )
        if not sources:
            sources = [QASourceRef(type="document", ref="docs/qa-agent-requirements.md")]
        return QAResult(
            answer=answer,
            confidence=0.86,
            answer_type="operations_guidance" if operations else "architecture_explanation",
            sources=sources,
            follow_up_questions=[] if operations else ["是否需要我把这个问题转成需求或测试策略？"],
            operations=operations,
        )


class RequirementsRemoteAgent(RemoteA2AAgent):
    def __init__(self, analyzer: HarnessRequirementsAnalyzer | None = None) -> None:
        self.analyzer = analyzer or HarnessRequirementsAnalyzer()
        super().__init__(
            AgentCard(
                agent_id="remote.requirements",
                name="Remote Requirements Agent",
                description=(
                    "Analyzes Harness requirement and architecture documents, "
                    "then produces RequirementAnalysisPackage."
                ),
                capabilities=[
                    AgentCapability(
                        name="requirements.analysis",
                        description="Analyze raw requirements and project documents",
                        input_schema={
                            "type": "object",
                            "properties": {
                                "message": {"type": "string"},
                                "documents": {
                                    "type": "array",
                                    "items": {
                                        "anyOf": [
                                            {"type": "string"},
                                            {
                                                "type": "object",
                                                "properties": {
                                                    "path": {"type": "string"},
                                                    "content": {"type": "string"},
                                                },
                                            },
                                        ]
                                    },
                                },
                                "content": {"type": "string"},
                            },
                            "required": ["message"],
                        },
                        output_schema=RequirementAnalysisPackage.model_json_schema(),
                    )
                ],
                output_schema=RequirementAnalysisPackage.model_json_schema(),
                metadata={"contract": "RequirementAnalysisPackage"},
            )
        )

    async def invoke(self, params: A2AInvokeParams) -> A2AInvokeResult:
        package = self.analyzer.analyze(
            message=params.message,
            input_data=params.input_data,
        )
        return completed_result(
            params,
            json.dumps(package.model_dump(mode="json"), ensure_ascii=False, indent=2),
            metadata={
                "contract": "RequirementAnalysisPackage",
                "summary": package.summary,
            },
        )


class TestCaseRemoteAgent(RemoteA2AAgent):
    def __init__(self) -> None:
        super().__init__(
            AgentCard(
                agent_id="remote.test_cases",
                name="Remote Test Case Agent",
                description="Turns RequirementSpec into TestCaseSet.",
                capabilities=[
                    AgentCapability(
                        name="test_case.generation",
                        description="Generate test cases",
                        input_schema=RequirementSpec.model_json_schema(),
                        output_schema=TestCaseSet.model_json_schema(),
                    )
                ],
                output_schema=TestCaseSet.model_json_schema(),
            )
        )

    async def invoke(self, params: A2AInvokeParams) -> A2AInvokeResult:
        cases = TestCaseSet(
            cases=[
                TestCase(
                    id="TC-001",
                    title="Validate parsed requirement",
                    requirement_ids=["REQ-001"],
                    steps=[
                        TestStep(
                            action="Exercise the requirement flow",
                            expected="The expected behavior is observed",
                        )
                    ],
                    tags=["generated", "smoke"],
                )
            ],
            coverage={"REQ-001": ["TC-001"]},
        )
        return completed_result(
            params,
            json.dumps(cases.model_dump(mode="json"), ensure_ascii=False, indent=2),
            metadata={"contract": "TestCaseSet"},
        )


class ScriptRemoteAgent(RemoteA2AAgent):
    def __init__(self) -> None:
        super().__init__(
            AgentCard(
                agent_id="remote.scripts",
                name="Remote Script Agent",
                description="Turns TestCaseSet into pytest ScriptBundle.",
                capabilities=[
                    AgentCapability(
                        name="script.generation",
                        description="Generate executable scripts",
                        input_schema=TestCaseSet.model_json_schema(),
                        output_schema=ScriptBundle.model_json_schema(),
                    )
                ],
                output_schema=ScriptBundle.model_json_schema(),
            )
        )

    async def invoke(self, params: A2AInvokeParams) -> A2AInvokeResult:
        bundle = ScriptBundle(
            files=[
                ScriptFile(
                    path="tests/test_generated.py",
                    content="def test_generated_requirement():\n    assert True\n",
                    test_case_ids=["TC-001"],
                )
            ],
            run_command="pytest tests/test_generated.py",
        )
        return completed_result(
            params,
            json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False, indent=2),
            metadata={"contract": "ScriptBundle"},
        )


class ExecutionRemoteAgent(RemoteA2AAgent):
    def __init__(self) -> None:
        super().__init__(
            AgentCard(
                agent_id="remote.execution",
                name="Remote Execution Agent",
                description="Submits script bundles to an execution backend.",
                capabilities=[
                    AgentCapability(
                        name="execution.run",
                        description="Execute generated scripts",
                        input_schema=ScriptBundle.model_json_schema(),
                        output_schema=ExecutionReport.model_json_schema(),
                    )
                ],
                risk_level="medium",
                output_schema=ExecutionReport.model_json_schema(),
            )
        )

    async def invoke(self, params: A2AInvokeParams) -> A2AInvokeResult:
        report = ExecutionReport(
            run_id="remote-run-001",
            status="passed",
            total=1,
            passed=1,
            failed=0,
        )
        return completed_result(
            params,
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
            metadata={"contract": "ExecutionReport"},
        )


class DiagnosisRemoteAgent(RemoteA2AAgent):
    def __init__(self) -> None:
        super().__init__(
            AgentCard(
                agent_id="remote.diagnosis",
                name="Remote Diagnosis Agent",
                description="Diagnoses failed execution reports.",
                capabilities=[
                    AgentCapability(
                        name="diagnosis.root_cause",
                        description="Diagnose execution failures",
                        input_schema=ExecutionReport.model_json_schema(),
                        output_schema=DiagnosisReport.model_json_schema(),
                    )
                ],
                output_schema=DiagnosisReport.model_json_schema(),
            )
        )

    async def invoke(self, params: A2AInvokeParams) -> A2AInvokeResult:
        report = DiagnosisReport(
            summary="No failure detected in example execution.",
            root_causes=[
                DiagnosisFinding(
                    severity="info",
                    title="Execution passed",
                    evidence="Example run status is passed.",
                    recommendation="No action required.",
                )
            ],
        )
        return completed_result(
            params,
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
            metadata={"contract": "DiagnosisReport"},
        )


def create_example_agents() -> list[RemoteA2AAgent]:
    return [
        QuestionClassifierRemoteAgent(),
        QAConversationRemoteAgent(),
        RequirementsRemoteAgent(),
        TestCaseRemoteAgent(),
        ScriptRemoteAgent(),
        ExecutionRemoteAgent(),
        DiagnosisRemoteAgent(),
    ]
