"""Knowledge base models."""

from enum import StrEnum
from typing import Any

from pydantic import Field

from harness.models.common import HarnessBaseModel


class KnowledgeCategory(StrEnum):
    TEST_TEMPLATE = "test_template"
    SCRIPT_PATTERN = "script_pattern"
    ERROR_PATTERN = "error_pattern"
    REQUIREMENT_RULE = "requirement_rule"
    DOMAIN_KNOWLEDGE = "domain_knowledge"
    GENERAL = "general"


class KnowledgeEntry(HarnessBaseModel):
    """A single entry in the knowledge base."""

    id: str
    title: str
    content: str
    category: KnowledgeCategory = KnowledgeCategory.GENERAL
    tags: list[str] = []
    source_file: str = ""
    metadata: dict = {}


class KnowledgeSourceType(StrEnum):
    REQUIREMENT = "requirement"
    ARCHITECTURE = "architecture"
    API_SPEC = "api_spec"
    TEST_ASSET = "test_asset"
    AUTOMATION_CODE = "automation_code"
    RUNBOOK = "runbook"
    OBSERVABILITY = "observability"
    INCIDENT = "incident"
    AGENT_ARTIFACT = "agent_artifact"
    GENERIC = "generic"


class RetrievalStrategy(StrEnum):
    KEYWORD = "keyword"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


class KnowledgeCollectionDraft(HarnessBaseModel):
    name: str
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeDocumentDraft(HarnessBaseModel):
    collection_name: str
    title: str
    content: str
    source_type: KnowledgeSourceType = KnowledgeSourceType.GENERIC
    source_uri: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    acl_tags: list[str] = Field(default_factory=list)
    chunk_size: int = 1600
    artifact_id: str | None = None
    artifact_version_id: str | None = None


class KnowledgeSearchRequest(HarnessBaseModel):
    query: str
    agent_id: str | None = None
    collections: list[str] = Field(default_factory=list)
    strategy: RetrievalStrategy = RetrievalStrategy.HYBRID
    limit: int = 8
    acl_tags: list[str] = Field(default_factory=list)


class KnowledgeSearchResult(HarnessBaseModel):
    chunk_id: str
    document_id: str
    collection_name: str
    title: str
    content: str
    score: float
    source_type: str
    source_uri: str
    artifact_id: str | None = None
    artifact_version_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentKnowledgeProfile(HarnessBaseModel):
    agent_id: str
    collections: list[str] = Field(default_factory=list)
    strategy: RetrievalStrategy = RetrievalStrategy.HYBRID
    limit: int = 8
    require_citations: bool = True
    write_back_artifacts: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
