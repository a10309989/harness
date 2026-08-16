"""Test script models."""

from enum import StrEnum

from harness.models.common import HarnessBaseModel, TimestampedModel


class ScriptType(StrEnum):
    PYTEST = "pytest"
    SELENIUM = "selenium"
    PLAYWRIGHT = "playwright"
    API_TEST = "api_test"
    LOCUST = "locust"
    CUSTOM = "custom"


class ScriptDependency(HarnessBaseModel):
    """A dependency required by a test script."""

    package: str
    version: str = "latest"
    import_path: str | None = None


class ScriptMetadata(HarnessBaseModel):
    """Metadata about a generated test script."""

    author: str = "harness-agent"
    framework_version: str = "0.1.0"
    generated_by: str = ""  # agent_id
    generated_from: str = ""  # test_case_id
    python_version: str = "3.11"


class TestScript(TimestampedModel):
    """A generated test script."""

    id: str
    test_case_id: str
    script_type: ScriptType
    title: str
    source_code: str
    dependencies: list[ScriptDependency] = []
    environment_vars: dict[str, str] = {}
    fixtures_needed: list[str] = []
    metadata: ScriptMetadata
    version: int = 1
    is_active: bool = True
