"""Built-in skill: Pytest test script generation."""

from harness.models.skill import SkillConfig
from harness.skills.base import BaseSkill


class PytestGeneratorSkill(BaseSkill):
    """Skill for generating pytest-compliant test scripts.

    Injects pytest-specific prompt guidance, validation tools, and
    reusable fixture/assertion pattern knowledge into the agent.
    """

    def __init__(self) -> None:
        config = SkillConfig(
            name="pytest_generator",
            version="1.0.0",
            description="Generate pytest-compliant test automation scripts with fixtures and parametrize",
            category="script_generation",
            target_agents=["script_generator"],
            prompt_contributions={
                "system_prompt_appendix": (
                    "## Pytest Generator Skill\n"
                    "You are skilled in generating pytest-compliant test scripts. Follow these rules:\n"
                    "- Use `@pytest.fixture` for test data management\n"
                    "- Use `@pytest.mark.parametrize` for data-driven tests\n"
                    "- Name all test functions with `test_` prefix\n"
                    "- Use plain `assert` statements (not unittest-style assertEqual)\n"
                    "- Follow AAA pattern: Arrange → Act → Assert\n"
                    "- Add docstrings to every test function\n"
                    "- Group related tests in classes prefixed with `Test`\n"
                ),
            },
            tools=[],
            knowledge_bases=[],
            vector_collections=[],
        )
        super().__init__(config)
