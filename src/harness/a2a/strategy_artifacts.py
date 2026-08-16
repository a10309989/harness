"""Generate and persist requirement-analysis strategy artifacts."""

from __future__ import annotations

from pathlib import Path

from harness.a2a.example_agents import RequirementsRemoteAgent
from harness.artifacts.service import ArtifactService
from harness.models.a2a import A2AInvokeParams
from harness.models.agent_contracts import RequirementAnalysisPackage
from harness.models.artifact import ArtifactDraft, ArtifactType, ArtifactVersionRef


async def generate_qa_agent_strategy_artifact(
    artifact_service: ArtifactService,
    *,
    session_id: str = "qa-agent-requirements",
    requirements_doc: str = "docs/qa-agent-requirements.md",
) -> ArtifactVersionRef:
    """Run the remote requirements analyzer and persist its output for test-case agents."""

    agent = RequirementsRemoteAgent()
    result = await agent.invoke(
        A2AInvokeParams(
            agent_id="remote.requirements",
            message="Analyze QA Conversation Agent requirements and generate a test strategy package.",
            session_id=session_id,
            input_data={"documents": [requirements_doc]},
        )
    )
    package = RequirementAnalysisPackage.model_validate_json(result.message)
    content = package.model_dump_json(indent=2)
    return await artifact_service.create(
        ArtifactDraft(
            artifact_type=ArtifactType.REQUIREMENT_ANALYSIS_PACKAGE,
            name="qa-agent-requirement-analysis-package",
            content=content,
            media_type="application/json",
            metadata={
                "contract": "RequirementAnalysisPackage",
                "source_document": requirements_doc,
                "consumer": "test_case_agent",
                "summary": package.summary,
            },
        )
    )


def qa_requirements_doc_exists(requirements_doc: str = "docs/qa-agent-requirements.md") -> bool:
    return Path(requirements_doc).exists()
