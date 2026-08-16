"""BaseSkill — a reusable domain capability package.

A Skill bundles Prompt templates, Knowledge, Tools, and Vector collections
into a single reusable package that can be bound to any Agent.
"""

from harness.models.skill import SkillConfig


class BaseSkill:
    """A reusable domain capability package.

    Skills are higher-level than Tools. While a Tool is an atomic operation
    (read a file, execute a command), a Skill is a complete domain capability
    (generate pytest scripts, debug selenium failures, match log patterns).
    """

    def __init__(self, config: SkillConfig) -> None:
        self.config = config
        self._tools: list = []
        self._knowledge_bases: list = []

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def version(self) -> str:
        return self.config.version

    @property
    def category(self) -> str:
        return self.config.category

    @property
    def prompt_contributions(self) -> dict[str, str]:
        """Prompt fragments to inject into an agent's system prompt."""
        return self.config.prompt_contributions

    @property
    def tools(self) -> list:
        """Tools provided by this skill."""
        return self._tools

    @property
    def knowledge_bases(self) -> list:
        """Knowledge bases provided by this skill."""
        return self.config.knowledge_bases

    @property
    def vector_collections(self) -> list:
        """Vector collections this skill needs."""
        return self.config.vector_collections

    def add_tool(self, tool: object) -> None:
        """Add a tool instance to this skill."""
        self._tools.append(tool)

    def is_compatible_with(self, agent_type: str) -> bool:
        """Check if this skill is compatible with a given agent type.

        Args:
            agent_type: The agent's ID or type string.

        Returns:
            True if the skill targets this agent type (or targets all).
        """
        if not self.config.target_agents:
            return True  # No restriction — compatible with all
        return agent_type in self.config.target_agents
