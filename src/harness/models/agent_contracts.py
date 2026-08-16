"""Standard contracts exchanged between test-lifecycle agents."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from harness.models.common import HarnessBaseModel


class StandardAgentRole(StrEnum):
    REQUIREMENTS_ANALYST = "requirements_analyst"
    TEST_CASE_GENERATOR = "test_case_generator"
    SCRIPT_GENERATOR = "script_generator"
    EXECUTION_AGENT = "execution_agent"
    DIAGNOSIS_AGENT = "diagnosis_agent"
    QUESTION_CLASSIFIER = "question_classifier"
    QA_CONVERSATION = "qa_conversation"


class QuestionIntent(StrEnum):
    GENERAL_QA = "general_qa"
    REQUIREMENTS = "requirements"
    TEST_CASE = "test_case"
    SCRIPT = "script"
    EXECUTION = "execution"
    DIAGNOSIS = "diagnosis"
    UNKNOWN = "unknown"


class ClassificationResult(HarnessBaseModel):
    intent: QuestionIntent
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    target_agent_id: str
    requires_workflow: bool = False
    risk_level: str = "low"


class QASourceRef(HarnessBaseModel):
    type: str = "document"
    ref: str


class QAResult(HarnessBaseModel):
    answer: str
    confidence: float = Field(ge=0, le=1)
    answer_type: str = "general_answer"
    sources: list[QASourceRef] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    operations: list[dict[str, Any]] = Field(default_factory=list)


class ContractRef(HarnessBaseModel):
    artifact_id: str | None = None
    version_id: str | None = None
    content_digest: str | None = None
    uri: str | None = None


class RequirementItem(HarnessBaseModel):
    id: str
    title: str
    description: str
    priority: str = "medium"
    acceptance_criteria: list[str] = Field(default_factory=list)
    source_ref: str | None = None


class RequirementSpec(HarnessBaseModel):
    requirements: list[RequirementItem] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    references: list[ContractRef] = Field(default_factory=list)


class NonFunctionalRequirement(HarnessBaseModel):
    id: str
    category: str
    description: str
    priority: str = "medium"
    acceptance_criteria: list[str] = Field(default_factory=list)
    source_ref: str | None = None


class RequirementTrace(HarnessBaseModel):
    requirement_id: str
    risk_ids: list[str] = Field(default_factory=list)
    test_focus: list[str] = Field(default_factory=list)
    recommended_test_levels: list[str] = Field(default_factory=list)


class TestStrategy(HarnessBaseModel):
    objective: str
    levels: list[str] = Field(default_factory=list)
    priority_model: str = "risk_based"
    entry_criteria: list[str] = Field(default_factory=list)
    exit_criteria: list[str] = Field(default_factory=list)
    coverage_targets: list[str] = Field(default_factory=list)
    regression_scope: list[str] = Field(default_factory=list)
    automation_approach: list[str] = Field(default_factory=list)


class MindMapNode(HarnessBaseModel):
    """One node of the requirement-analysis mind-map tree.

    ``children`` makes the node a branch; a node with no children is a leaf.
    """

    id: str
    label: str
    children: list[MindMapNode] = Field(default_factory=list)


class RequirementAnalysisPackage(HarnessBaseModel):
    requirement_spec: RequirementSpec
    non_functional_requirements: list[NonFunctionalRequirement] = Field(default_factory=list)
    test_strategy: TestStrategy
    traceability: list[RequirementTrace] = Field(default_factory=list)
    summary: str = ""
    mind_map: MindMapNode | None = None


class TestStep(HarnessBaseModel):
    action: str
    expected: str
    data: dict[str, Any] = Field(default_factory=dict)


class TestCase(HarnessBaseModel):
    id: str
    title: str
    requirement_ids: list[str] = Field(default_factory=list)
    priority: str = "medium"
    preconditions: list[str] = Field(default_factory=list)
    steps: list[TestStep] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class TestCaseSet(HarnessBaseModel):
    cases: list[TestCase] = Field(default_factory=list)
    coverage: dict[str, list[str]] = Field(default_factory=dict)
    references: list[ContractRef] = Field(default_factory=list)


class ScriptFile(HarnessBaseModel):
    path: str
    language: str = "python"
    framework: str = "pytest"
    content: str
    test_case_ids: list[str] = Field(default_factory=list)


class ScriptBundle(HarnessBaseModel):
    files: list[ScriptFile] = Field(default_factory=list)
    install_command: str | None = None
    run_command: str | None = None
    references: list[ContractRef] = Field(default_factory=list)


class ExecutionReport(HarnessBaseModel):
    run_id: str
    status: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    duration_seconds: float | None = None
    logs: list[ContractRef] = Field(default_factory=list)
    artifacts: list[ContractRef] = Field(default_factory=list)


class DiagnosisFinding(HarnessBaseModel):
    severity: str
    title: str
    evidence: str
    recommendation: str
    related_test_case_ids: list[str] = Field(default_factory=list)


class DiagnosisReport(HarnessBaseModel):
    summary: str
    root_causes: list[DiagnosisFinding] = Field(default_factory=list)
    mitigations: list[str] = Field(default_factory=list)
    references: list[ContractRef] = Field(default_factory=list)
