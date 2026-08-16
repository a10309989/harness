"""FastAPI dependency injection."""

from fastapi import Request

from harness.api.config_store import ConfigStore
from harness.core.events import EventBus
from harness.core.registry import AgentRegistry
from harness.core.session import SessionManager
from harness.db.protocols import DatabaseProtocol
from harness.mcp.manager import MCPManager
from harness.observability.audit import AuditService
from harness.observability.agent_events import AgentEventService
from harness.artifacts.service import ArtifactService
from harness.policy.engine import PolicyEngine
from harness.workflow.human_tasks import HumanTaskService
from harness.workflow.service import WorkflowService
from harness.security.auth import AuthService
from harness.skills.registry import SkillRegistry
from harness.runtime.settings import RuntimeSettings
from harness.knowledge.service import KnowledgeService


def get_database(request: Request) -> DatabaseProtocol:
    """Get the Database from app state."""
    return request.app.state.database


def get_config_store(request: Request) -> ConfigStore:
    """Get the ConfigStore from app state."""
    return request.app.state.config_store


def get_session_manager(request: Request) -> SessionManager:
    """Get the SessionManager from app state."""
    return request.app.state.session_manager


def get_agent_registry(request: Request) -> AgentRegistry:
    """Get the AgentRegistry from app state."""
    return request.app.state.agent_registry


def get_event_bus(request: Request) -> EventBus:
    """Get the EventBus from app state."""
    return request.app.state.event_bus


def get_skill_registry(request: Request) -> SkillRegistry:
    """Get the SkillRegistry from app state."""
    return request.app.state.skill_registry


def get_mcp_manager(request: Request) -> MCPManager:
    """Get the MCPManager from app state."""
    return request.app.state.mcp_manager


def get_audit_service(request: Request) -> AuditService:
    return request.app.state.audit_service


def get_agent_event_service(request: Request) -> AgentEventService:
    return request.app.state.agent_event_service


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def get_artifact_service(request: Request) -> ArtifactService:
    return request.app.state.artifact_service


def get_knowledge_service(request: Request) -> KnowledgeService:
    return request.app.state.knowledge_service


def get_policy_engine(request: Request) -> PolicyEngine:
    return request.app.state.policy_engine


def get_workflow_service(request: Request) -> WorkflowService:
    return request.app.state.workflow_service


def get_human_task_service(request: Request) -> HumanTaskService:
    return request.app.state.human_task_service


def get_runtime_settings(request: Request) -> RuntimeSettings:
    """Get immutable runtime configuration from app state."""
    return request.app.state.settings
