"""Predefined ChromaDB collection names for the framework."""

from enum import StrEnum


class VectorCollection(StrEnum):
    """Standard collection names used across the framework."""

    REQUIREMENTS = "requirements"
    TEST_CASES = "test_cases"
    TEST_SCRIPTS = "test_scripts"
    ERROR_PATTERNS = "error_patterns"
    KNOWLEDGE_ARTICLES = "knowledge"
    SESSION_HISTORY = "session_history"
    LOG_ENTRIES = "log_entries"
