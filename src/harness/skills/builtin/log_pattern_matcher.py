"""Built-in skill: Log pattern matching for test failure diagnosis."""

from harness.models.skill import SkillConfig
from harness.skills.base import BaseSkill


class LogPatternMatcherSkill(BaseSkill):
    """Skill for matching error patterns in test execution logs.

    Injects log analysis prompt guidance and error pattern knowledge
    into log analysis agents for faster, more accurate diagnosis.
    """

    def __init__(self) -> None:
        config = SkillConfig(
            name="log_pattern_matcher",
            version="2.0.0",
            description="Match error patterns in test logs for rapid root cause identification",
            category="log_analysis",
            target_agents=["log_analyst"],
            prompt_contributions={
                "system_prompt_appendix": (
                    "## Log Pattern Matcher Skill\n"
                    "You are skilled in analyzing test execution logs. Follow this approach:\n"
                    "1. Identify error signatures (exception type, error message patterns)\n"
                    "2. Normalize the error (remove timestamps, IDs, temp paths)\n"
                    "3. Match against known error pattern database\n"
                    "4. Trace the error to its root cause (not just the symptom)\n"
                    "5. Suggest concrete fix actions with code snippets when possible\n\n"
                    "Common error categories to check:\n"
                    "- NullPointer/AttributeError: uninitialized data or missing fixtures\n"
                    "- TimeoutError: network latency, slow selectors, insufficient waits\n"
                    "- AssertionError: test logic mismatch, environment differences\n"
                    "- ImportError: missing dependencies or wrong Python path\n"
                    "- ConnectionError: service unavailable, wrong URLs, auth issues\n"
                ),
            },
            tools=[],
            knowledge_bases=[],
            vector_collections=[],
        )
        super().__init__(config)
