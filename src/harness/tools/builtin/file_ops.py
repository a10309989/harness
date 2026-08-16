"""Built-in tool: File read/write operations."""

from pathlib import Path

from harness.models.tool import ToolResult
from harness.models.policy import RiskLevel
from harness.models.workflow import IdempotencyMode
from harness.tools.base import BaseTool


class FileReader(BaseTool):
    """Read file contents with size and extension filtering."""

    name = "file_reader"
    description = "Read the contents of a file from the filesystem."
    parameters_schema = {
        "path": {
            "type": "string",
            "description": "Path to the file to read",
            "required": True,
        },
        "encoding": {
            "type": "string",
            "description": "File encoding (default: utf-8)",
            "default": "utf-8",
        },
    }
    risk_level = RiskLevel.LOW
    risk_tags = frozenset({"filesystem", "read"})
    idempotency_mode = IdempotencyMode.SAFE_RETRY

    def __init__(self, allowed_extensions: list[str] | None = None, max_file_size_mb: int = 10) -> None:
        super().__init__()
        self.allowed_extensions = allowed_extensions or [".txt", ".log", ".xml", ".json", ".yaml", ".yml", ".md", ".py", ".html", ".csv"]
        self.max_file_size_mb = max_file_size_mb

    async def execute(self, path: str, encoding: str = "utf-8") -> ToolResult:
        file_path = Path(path)
        if not file_path.exists():
            return ToolResult(success=False, error=f"File not found: {path}")
        if file_path.suffix not in self.allowed_extensions:
            return ToolResult(success=False, error=f"Extension not allowed: {file_path.suffix}")
        if file_path.stat().st_size > self.max_file_size_mb * 1024 * 1024:
            return ToolResult(success=False, error=f"File exceeds {self.max_file_size_mb}MB limit")

        content = file_path.read_text(encoding=encoding)
        return ToolResult(success=True, data={"path": path, "content": content, "size_bytes": len(content)})


class FileWriter(BaseTool):
    """Write content to a file."""

    name = "file_writer"
    description = "Write content to a file on the filesystem."
    parameters_schema = {
        "path": {
            "type": "string",
            "description": "Path to write the file to",
            "required": True,
        },
        "content": {
            "type": "string",
            "description": "Content to write",
            "required": True,
        },
        "encoding": {
            "type": "string",
            "description": "File encoding (default: utf-8)",
            "default": "utf-8",
        },
    }
    risk_level = RiskLevel.MEDIUM
    risk_tags = frozenset({"filesystem", "write"})
    idempotency_mode = IdempotencyMode.KEYED

    async def execute(self, path: str, content: str, encoding: str = "utf-8") -> ToolResult:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding=encoding)
        return ToolResult(success=True, data={"path": path, "size_bytes": len(content)})
