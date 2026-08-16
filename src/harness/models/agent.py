"""Agent configuration and state models."""

from enum import StrEnum

from harness.models.common import HarnessBaseModel


class AgentState(StrEnum):
    IDLE = "idle"
    PROCESSING = "processing"
    WAITING = "waiting"
    ERROR = "error"
    TERMINATED = "terminated"


class ExecutionMode(StrEnum):
    PLAN = "plan"
    REACT = "react"
    HYBRID = "hybrid"


class AgentCapability(HarnessBaseModel):
    """Declares what an agent can do — used for intent routing."""

    name: str
    description: str
    keywords: list[str] = []
    input_schema: dict | None = None
    output_schema: dict | None = None


class StopCondition(HarnessBaseModel):
    """Condition to stop a ReAct loop."""

    type: str  # "token_limit", "no_progress_rounds", "max_time_seconds"
    value: int


class LLMConfig(HarnessBaseModel):
    """LLM provider configuration for an agent."""

    provider: str = "anthropic"
    model: str = "claude-sonnet-4-20250514"
    temperature: float = 0.2
    max_tokens: int = 4096
    system_prompt: str | None = None
    fallback_providers: list[str] = []
    provider_name: str = ""  # name of saved provider in ConfigStore to bind to


class ContextConfig(HarnessBaseModel):
    """Context window configuration."""

    max_turns: int = 20
    include_summary: bool = True


class MemoryConfig(HarnessBaseModel):
    """Memory configuration."""

    storage_path: str = "data/memory"
    short_term_ttl_seconds: int = 3600
    long_term_enabled: bool = True


class VectorConfig(HarnessBaseModel):
    """Vector store configuration."""

    collection_name: str
    embedding_model: str = "text-embedding-3-small"
    embedding_provider: str = "openai"
    persist_directory: str = "data/chroma"


class KnowledgeBaseConfig(HarnessBaseModel):
    """Knowledge base source configuration."""

    name: str
    source_path: str
    collection_name: str
    index_on_startup: bool = False


class ToolConfig(HarnessBaseModel):
    """Tool configuration."""

    name: str
    enabled: bool = True
    config: dict = {}
    timeout_seconds: int = 30


class VectorCollectionConfig(HarnessBaseModel):
    """Vector collection reference for a skill."""

    name: str
    description: str = ""


class AgentConfig(HarnessBaseModel):
    """Complete agent configuration aggregating all capabilities."""

    id: str
    name: str
    description: str = ""

    # Execution mode
    execution_mode: ExecutionMode = ExecutionMode.PLAN
    react_max_iterations: int = 15
    react_stop_conditions: list[StopCondition] = []

    # Capabilities
    capabilities: list[AgentCapability] = []

    # LLM
    llm: LLMConfig

    # Core capabilities
    context: ContextConfig = ContextConfig()
    memory: MemoryConfig = MemoryConfig()
    vector: VectorConfig

    # Knowledge & tools
    knowledge_bases: list[KnowledgeBaseConfig] = []
    tools: list[ToolConfig] = []

    # Skill bindings
    bind_skills: list[str] = []

    # MCP connections — list of MCPServerConfig dicts
    mcp_servers: list = []

    # Prompts
    prompts: dict[str, str] = {}
    min_confidence: float = 0.6
