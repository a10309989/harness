"""Built-in tool: Shell command execution with mandatory allowlist."""

import asyncio
import os
import shlex

from harness.models.tool import ToolResult
from harness.models.policy import RiskLevel
from harness.models.workflow import IdempotencyMode
from harness.tools.base import BaseTool


class ShellExecutor(BaseTool):
    """Execute shell commands with mandatory allowlist filtering.

    IMPORTANT: allowed_commands MUST be explicitly configured.
    An empty allowlist rejects all commands for security.
    """

    name = "shell_executor"
    description = "Execute a shell command and return stdout/stderr."
    parameters_schema = {
        "command": {
            "type": "string",
            "description": "Shell command to execute",
            "required": True,
        },
        "timeout_seconds": {
            "type": "integer",
            "description": "Max execution time in seconds",
            "default": 60,
        },
    }
    requires_sandbox = True
    risk_level = RiskLevel.HIGH
    risk_tags = frozenset({"shell", "subprocess"})
    idempotency_mode = IdempotencyMode.NON_IDEMPOTENT

    def __init__(self, allowed_commands: list[str] | None = None) -> None:
        super().__init__()
        self.allowed_commands = allowed_commands or []
        self._validate_allowlist()

    def _validate_allowlist(self) -> None:
        """Ensure the allowlist is explicitly configured for safety."""
        if not self.allowed_commands:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                "ShellExecutor initialized with empty allowlist — ALL commands will be rejected. "
                "Configure 'allowed_commands' in your agent YAML config to enable shell execution."
            )

    async def execute(self, command: str, timeout_seconds: int = 60) -> ToolResult:
        # Always enforce allowlist — empty means nothing is allowed
        if not self.allowed_commands:
            return ToolResult(
                success=False,
                error="ShellExecutor: No allowed commands configured. Add 'allowed_commands' to the tool config.",
            )

        cmd_name = shlex.split(command)[0] if command else ""
        if cmd_name not in self.allowed_commands:
            return ToolResult(
                success=False,
                error=f"Command '{cmd_name}' not in allowlist. Allowed: {self.allowed_commands}",
            )

        cmd_parts = shlex.split(command)
        if cmd_name == "echo":
            return ToolResult(
                success=True,
                data={"exit_code": 0, "stdout": " ".join(cmd_parts[1:]) + "\n", "stderr": ""},
            )

        try:
            # Use create_subprocess_exec (NOT shell) for safety
            proc = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)

            return ToolResult(
                success=proc.returncode == 0,
                data={
                    "exit_code": proc.returncode,
                    "stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace"),
                },
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return ToolResult(success=False, error=f"Command timed out after {timeout_seconds}s")
        except FileNotFoundError:
            return ToolResult(success=False, error=f"Command not found: {cmd_name}")
        except Exception as e:
            return ToolResult(success=False, error=f"Shell execution error: {e}")
