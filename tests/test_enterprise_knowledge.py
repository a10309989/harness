import pytest

from harness.artifacts.service import ArtifactService
from harness.artifacts.storage import LocalCAS
from harness.knowledge.service import KnowledgeService
from harness.models.artifact import ArtifactDraft, ArtifactType
from harness.models.knowledge import KnowledgeDocumentDraft, KnowledgeSearchRequest


@pytest.fixture
async def knowledge_service(db, tmp_path):
    artifacts = ArtifactService(db, LocalCAS(str(tmp_path / "cas")))
    service = KnowledgeService(db, artifacts)
    await service.initialize_defaults()
    yield db, artifacts, service


async def test_default_agent_profiles_are_seeded(knowledge_service):
    _, _, service = knowledge_service

    profile = await service.get_agent_profile("remote.diagnosis")
    collections = await service.list_collections()

    assert profile is not None
    assert "observability" in profile.collections
    assert "runbooks" in profile.collections
    assert {collection["name"] for collection in collections} >= {
        "requirements",
        "test_assets",
        "agent_artifacts",
    }


async def test_agent_profile_scopes_search_and_records_retrieval_audit(knowledge_service):
    db, _, service = knowledge_service
    await service.ingest_document(
        KnowledgeDocumentDraft(
            collection_name="requirements",
            title="登录需求",
            content="用户登录需要支持验证码、密码错误锁定和审计记录。",
            source_uri="doc://requirements/login",
        )
    )
    await service.ingest_document(
        KnowledgeDocumentDraft(
            collection_name="runbooks",
            title="部署手册",
            content="部署失败时先查看 Runner 队列和 Kubernetes 事件。",
            source_uri="doc://runbooks/deploy",
        )
    )

    result = await service.search(
        KnowledgeSearchRequest(
            query="登录 验证码 锁定",
            agent_id="remote.requirements",
        )
    )
    audits = await db.fetch_all("SELECT * FROM retrieval_audit_events", ())

    assert result["count"] == 1
    assert result["results"][0]["collection_name"] == "requirements"
    assert audits[0]["agent_id"] == "remote.requirements"
    assert audits[0]["result_count"] == 1


async def test_artifact_write_back_indexes_agent_output(knowledge_service):
    _, artifacts, service = knowledge_service
    ref = await artifacts.create(
        ArtifactDraft(
            artifact_type=ArtifactType.TEST_STRATEGY,
            name="登录测试策略",
            content='{"strategy":"覆盖验证码、锁定、审计链路"}',
            media_type="application/json",
        )
    )

    indexed = await service.write_artifact_to_knowledge(ref, title="登录测试策略产物")
    result = await service.search(
        KnowledgeSearchRequest(
            query="验证码 审计",
            collections=["agent_artifacts"],
        )
    )

    assert indexed["chunk_count"] == 1
    assert result["count"] == 1
    assert result["results"][0]["artifact_version_id"] == ref.version_id


async def test_documents_can_be_listed_and_loaded_with_chunks(knowledge_service):
    _, _, service = knowledge_service
    ingested = await service.ingest_document(
        KnowledgeDocumentDraft(
            collection_name="runbooks",
            title="Runner 故障处理",
            content="Runner 队列积压时检查 Redis Streams pending 和 DLQ。",
            source_uri="runbook://runner/queue",
        )
    )

    documents = await service.list_documents("runbooks")
    detail = await service.get_document(ingested["document_id"])

    assert len(documents) == 1
    assert documents[0]["status"] == "indexed"
    assert documents[0]["chunk_count"] == 1
    assert detail is not None
    assert detail["chunks"][0]["content"].startswith("Runner")


async def test_deleted_document_is_hidden_from_lists_details_and_search(knowledge_service):
    _, _, service = knowledge_service
    ingested = await service.ingest_document(
        KnowledgeDocumentDraft(
            collection_name="requirements",
            title="待删除需求",
            content="delete-smoke-marker should disappear from retrieval after soft delete.",
            source_uri="doc://requirements/delete-smoke",
        )
    )

    assert await service.delete_document(ingested["document_id"]) is True
    assert await service.delete_document(ingested["document_id"]) is False

    documents = await service.list_documents("requirements")
    detail = await service.get_document(ingested["document_id"])
    result = await service.search(
        KnowledgeSearchRequest(
            query="delete-smoke-marker",
            collections=["requirements"],
        )
    )

    assert documents == []
    assert detail is None
    assert result["count"] == 0
