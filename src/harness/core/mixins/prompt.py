"""PromptMixin — Jinja2 template engine with variable injection."""

from abc import ABC
from typing import Any

from jinja2 import BaseLoader, Environment, StrictUndefined

from harness.models.agent import AgentConfig


class PromptMixin(ABC):
    """Capability: prompt template management with variable injection.

    Provides Jinja2-based template rendering for system prompts,
    user prompts, and any other text generation needs.
    """

    prompt_templates: dict[str, str]
    prompt_env: Environment
    _system_prompt: str | None = None
    _skill_prompt_appendix: list[str]

    def _init_prompt(self, config: AgentConfig) -> None:
        """Initialize the prompt engine from agent configuration.

        Args:
            config: The agent's full configuration containing prompt templates.
        """
        self.prompt_env = Environment(loader=BaseLoader(), undefined=StrictUndefined)
        self.prompt_templates = dict(config.prompts)
        self._skill_prompt_appendix = []

        # Extract system prompt if present
        if "system" in self.prompt_templates:
            self._system_prompt = self.prompt_templates.pop("system")

    def render_prompt(self, template_name: str, variables: dict[str, Any] | None = None) -> str:
        """Render a named template with variable injection.

        Args:
            template_name: Name of the template to render.
            variables: Variables to inject into the template.

        Returns:
            The rendered prompt string.

        Raises:
            KeyError: If the template name is not found.
            jinja2.UndefinedError: If a required variable is missing.
        """
        variables = variables or {}
        template_str = self.prompt_templates.get(template_name)
        if template_str is None:
            raise KeyError(f"Prompt template '{template_name}' not found. Available: {list(self.prompt_templates)}")
        template = self.prompt_env.from_string(template_str)
        return template.render(**variables)

    def append_system_prompt(self, appendix: dict[str, str] | str) -> None:
        """Append content to the system prompt (used by SkillBinder).

        Args:
            appendix: Either a string to append, or a dict with
                      'system_prompt_appendix' key (from SkillConfig).
        """
        if isinstance(appendix, dict):
            extra = appendix.get("system_prompt_appendix", "")
        else:
            extra = appendix
        if extra:
            self._skill_prompt_appendix.append(extra)

    def build_system_prompt(self, context: dict[str, Any] | None = None) -> str:
        """Build the complete system prompt including skill contributions.

        Args:
            context: Variables to inject into the system prompt template.

        Returns:
            The fully assembled system prompt string.
        """
        context = context or {}
        parts = []

        if self._system_prompt:
            try:
                template = self.prompt_env.from_string(self._system_prompt)
                parts.append(template.render(**context))
            except Exception:
                parts.append(self._system_prompt)

        parts.extend(self._skill_prompt_appendix)
        return "\n\n".join(parts)

    def set_system_prompt(self, prompt: str) -> None:
        """Replace the system prompt entirely."""
        self._system_prompt = prompt
