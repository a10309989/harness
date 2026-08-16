"""Log entry and diagnostic models."""

from enum import StrEnum

from harness.models.common import HarnessBaseModel


class LogSeverity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class LogEntry(HarnessBaseModel):
    """A single log entry."""

    timestamp: str
    severity: LogSeverity
    source: str = ""
    message: str
    stack_trace: str | None = None
    context: dict = {}


class FixSuggestion(HarnessBaseModel):
    """A suggested fix from AI diagnosis."""

    suggestion: str
    confidence: float
    code_patch: str | None = None
    references: list[str] = []


class DiagnosticResult(HarnessBaseModel):
    """AI diagnosis result for a failed execution."""

    id: str
    execution_result_id: str
    root_cause: str
    confidence: float
    affected_components: list[str] = []
    error_signature: str | None = None
    matching_patterns: list[str] = []
    suggested_fixes: list[FixSuggestion] = []
