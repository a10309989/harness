"""Intent classification and routing models."""

from enum import StrEnum

from harness.models.common import HarnessBaseModel


class IntentCategory(StrEnum):
    REQUIREMENTS_ANALYSIS = "requirements_analysis"
    TEST_CASE_GENERATION = "test_case_generation"
    SCRIPT_GENERATION = "script_generation"
    SCHEDULE_EXECUTION = "schedule_execution"
    LOG_ANALYSIS = "log_analysis"
    PLANNING = "planning"
    GENERAL = "general"


class IntentClassification(HarnessBaseModel):
    """Result of intent classification."""

    intent: IntentCategory
    confidence: float  # 0.0 - 1.0
    reasoning: str = ""
    extracted_entities: dict = {}


class IntentRoute(HarnessBaseModel):
    """Route mapping from intent to target agent."""

    target_agent: str
    pre_hooks: list[str] = []
    post_hooks: list[str] = []
    priority: int = 0
