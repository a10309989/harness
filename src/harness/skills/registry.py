"""SkillRegistry — global skill registration and discovery."""

from pathlib import Path
from typing import Any

import yaml

from harness.models.skill import SkillConfig
from harness.skills.base import BaseSkill

# Import builtin skills
from harness.skills.builtin.pytest_generator import PytestGeneratorSkill
from harness.skills.builtin.log_pattern_matcher import LogPatternMatcherSkill


class SkillRegistry:
    """Global registry for all available skills.

    Skills are registered once and can be bound to multiple agents.
    """

    _skills: dict[str, BaseSkill]
    _categories: dict[str, list[str]]

    def __init__(self) -> None:
        self._skills = {}
        self._categories = {}
        self._register_builtins()

    def register(self, skill: BaseSkill) -> None:
        """Register a skill instance.

        Args:
            skill: A BaseSkill instance.
        """
        self._skills[skill.name] = skill
        cat = skill.category
        if cat not in self._categories:
            self._categories[cat] = []
        if skill.name not in self._categories[cat]:
            self._categories[cat].append(skill.name)

    def get(self, name: str) -> BaseSkill:
        """Get a skill by name.

        Args:
            name: Skill name.

        Returns:
            The BaseSkill instance.

        Raises:
            KeyError: If the skill is not found.
        """
        if name not in self._skills:
            raise KeyError(f"Skill '{name}' not found. Available: {self.list_all()}")
        return self._skills[name]

    def list_all(self) -> list[str]:
        """List all registered skill names."""
        return list(self._skills.keys())

    def list_by_category(self, category: str) -> list[BaseSkill]:
        """List skills in a given category.

        Args:
            category: Skill category name.

        Returns:
            List of BaseSkill instances.
        """
        names = self._categories.get(category, [])
        return [self._skills[n] for n in names if n in self._skills]

    def list_compatible(self, agent_type: str) -> list[BaseSkill]:
        """List skills compatible with a given agent type.

        Args:
            agent_type: The agent's ID.

        Returns:
            List of compatible BaseSkill instances.
        """
        return [s for s in self._skills.values() if s.is_compatible_with(agent_type)]

    def load_from_directory(self, directory: str) -> int:
        """Load skills from YAML config files in a directory.

        Args:
            directory: Path to a directory containing skill YAML files.

        Returns:
            Number of skills loaded.
        """
        path = Path(directory)
        if not path.exists():
            return 0

        count = 0
        for yaml_file in path.rglob("*.yaml"):
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and "name" in data:
                    config = SkillConfig(**data)
                    skill = BaseSkill(config)
                    self.register(skill)
                    count += 1
            except Exception:
                pass

        return count

    def _register_builtins(self) -> None:
        """Register built-in skills."""
        self.register(PytestGeneratorSkill())
        self.register(LogPatternMatcherSkill())
