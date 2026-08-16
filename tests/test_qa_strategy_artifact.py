from harness.a2a.strategy_artifacts import generate_qa_agent_strategy_artifact
from harness.artifacts.service import ArtifactService
from harness.artifacts.storage import LocalCAS
from harness.models.artifact import ArtifactType


async def test_generate_qa_agent_strategy_artifact_persists_package(db, tmp_path):
    service = ArtifactService(db, LocalCAS(str(tmp_path / "cas")))

    ref = await generate_qa_agent_strategy_artifact(service)
    artifact = await service.get(ref.artifact_id)
    version, content = await service.read_version(ref.version_id)

    assert artifact["artifact_type"] == ArtifactType.REQUIREMENT_ANALYSIS_PACKAGE
    assert version["media_type"] == "application/json"
    assert b"RequirementAnalysisPackage" not in content
    assert b"test_strategy" in content
    await db.close()
