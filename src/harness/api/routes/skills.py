"""Skill management API routes."""

from fastapi import APIRouter, Depends, HTTPException

from harness.api.deps import get_skill_registry
from harness.security.dependencies import require_permission

router = APIRouter(dependencies=[Depends(require_permission("skill:read"))])


@router.get("")
async def list_skills(registry=Depends(get_skill_registry)):
    """List all available skills."""
    skills_list = []
    for name in registry.list_all():
        skill = registry.get(name)
        skills_list.append({
            "name": skill.name,
            "version": skill.version,
            "category": skill.category,
            "target_agents": skill.config.target_agents,
        })
    return {"skills": skills_list, "count": len(skills_list)}


@router.get("/{name}")
async def get_skill(name: str, registry=Depends(get_skill_registry)):
    """Get skill details."""
    try:
        skill = registry.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    return {
        "name": skill.name,
        "version": skill.version,
        "description": skill.config.description,
        "category": skill.category,
        "target_agents": skill.config.target_agents,
        "prompt_contributions": skill.prompt_contributions,
        "tools": [t.name for t in skill.config.tools],
        "knowledge_bases": [kb.name for kb in skill.config.knowledge_bases],
        "vector_collections": [vc.name for vc in skill.config.vector_collections],
    }
