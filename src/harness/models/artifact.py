"""Immutable artifact and structured agent outcome models."""

from enum import StrEnum
from typing import Any

from harness.models.common import HarnessBaseModel


class ArtifactType(StrEnum):
    REQUIREMENT_ANALYSIS = "requirement_analysis"
    REQUIREMENT_ANALYSIS_PACKAGE = "requirement_analysis_package"
    TEST_STRATEGY = "test_strategy"
    TEST_SCENARIO_SET = "test_scenario_set"
    TEST_CASE_SET = "test_case_set"
    TEST_SCRIPT = "test_script"
    EXECUTION_LOG = "execution_log"
    EXECUTION_REPORT = "execution_report"
    DIAGNOSIS = "diagnosis"
    GENERIC_DOCUMENT = "generic_document"
    WORKFLOW_CHECKPOINT = "workflow_checkpoint"
    EXECUTION_PLAN = "execution_plan"
    MIND_MAP = "mind_map"


class ArtifactDraft(HarnessBaseModel):
    artifact_type: ArtifactType
    name: str
    content: str | bytes
    media_type: str = "text/plain"
    metadata: dict[str, Any] = {}
    artifact_id: str | None = None


class ArtifactVersionRef(HarnessBaseModel):
    artifact_id: str
    version_id: str
    version_number: int
    content_digest: str
    media_type: str
    size_bytes: int


class ArtifactDisplayRef(ArtifactVersionRef):
    artifact_type: str = ""
    name: str = ""
    actions: list[str] = []
    metadata: dict[str, Any] = {}


class AgentOutcome(HarnessBaseModel):
    message: str
    status: str = "completed"
    artifacts: list[ArtifactVersionRef] = []
    artifact_cards: list[ArtifactDisplayRef] = []
    metadata: dict[str, Any] = {}
