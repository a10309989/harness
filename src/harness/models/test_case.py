"""Test case models."""

from enum import StrEnum

from harness.models.common import HarnessBaseModel, TimestampedModel


class Priority(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TestCategory(StrEnum):
    FUNCTIONAL = "functional"
    INTEGRATION = "integration"
    PERFORMANCE = "performance"
    SECURITY = "security"
    REGRESSION = "regression"
    E2E = "end_to_end"
    SMOKE = "smoke"


class TestStep(HarnessBaseModel):
    """A single step within a test case."""

    step_number: int
    action: str
    expected_result: str
    test_data: dict | None = None
    preconditions: list[str] = []
    postconditions: list[str] = []


class TestCase(TimestampedModel):
    """A test case definition."""

    id: str
    title: str
    description: str = ""
    priority: Priority = Priority.MEDIUM
    category: TestCategory = TestCategory.FUNCTIONAL
    tags: list[str] = []
    preconditions: list[str] = []
    steps: list[TestStep] = []
    expected_outcome: str = ""
    requirements_ref: list[str] = []
    automation_status: str = "not_automated"
    estimated_duration_seconds: int | None = None


class TestScenario(HarnessBaseModel):
    """Groups related test cases under a scenario."""

    id: str
    name: str
    description: str = ""
    test_cases: list[TestCase] = []
    requirements_ref: list[str] = []
