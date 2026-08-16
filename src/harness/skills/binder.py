"""SkillBinder — injects skills into an agent at initialization time."""

import logging
from typing import TYPE_CHECKING

from harness.skills.registry import SkillRegistry

if TYPE_CHECKING:
    from harness.core.agent import BaseAgent

logger = logging.getLogger(__name__)


class SkillBinder:
    """Binds skills to an agent, injecting prompt, tools, knowledge, and vector collections.

    Called during agent initialization. Reads the agent's bind_skills config
    and applies each skill's contributions to the corresponding mixin.
    """

    @classmethod
    def bind(cls, agent: "BaseAgent", skill_names: list[str], registry: SkillRegistry) -> None:
        """Inject all specified skills into an agent.

        Args:
            agent: The agent instance to bind skills to.
            skill_names: List of skill names from AgentConfig.bind_skills.
            registry: The global SkillRegistry.
        """
        for name in skill_names:
            try:
                skill = registry.get(name)
                cls._apply_skill(agent, skill)
                logger.info(f"Bound skill '{name}' to agent '{agent.agent_name}'")
            except KeyError:
                logger.warning(f"Skill '{name}' not found in registry — skipping")

    @classmethod
    def _apply_skill(cls, agent: "BaseAgent", skill) -> None:
        """Apply a single skill's contributions to an agent.

        Args:
            agent: The target agent.
            skill: The BaseSkill instance.
        """
        # 1. Inject prompt fragments into system prompt
        if skill.prompt_contributions:
            agent.append_system_prompt(skill.prompt_contributions)

        # 2. Register skill tools into agent's ToolRegistry
        for tool in skill.tools:
            try:
                agent.tool_registry.register(tool)
            except Exception as e:
                logger.warning(f"Failed to register skill tool '{tool.name}': {e}")

        # 3. Attach knowledge bases
        for kb_config in skill.knowledge_bases:
            agent.attach_knowledge_base(kb_config)

        # 4. Connect vector collections
        for vc_config in skill.vector_collections:
            agent.attach_vector_collection(vc_config)
