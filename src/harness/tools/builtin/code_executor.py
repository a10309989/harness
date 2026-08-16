"""Built-in tool: Execute Python/pytest scripts in a subprocess."""

import asyncio
import os
import tempfile
from pathlib import Path

from harness.models.tool import ToolResult
from harness.models.policy import RiskLevel
from harness.models.workflow import IdempotencyMode
from harness.tools.base import BaseTool


class CodeExecutor(BaseTool):
    """Execute Python/pytest test scripts in a sandboxed subprocess."""

    name = "code_executor"
    description = "Execute Python or pytest test scripts and return the output."
    parameters_schema = {
        "code": {
            "type": "string",
            "description": "Python source code to execute",
            "required": True,
        },
        "test_framework": {
            "type": "string",
            "enum": ["pytest", "unittest", "plain"],
            "description": "Test framework to use",
            "default": "pytest",
        },
        "timeout_seconds": {
            "type": "integer",
            "description": "Max execution time in seconds",
            "default": 300,
        },
    }
    requires_sandbox = True
    risk_level = RiskLevel.HIGH
    risk_tags = frozenset({"code-execution", "subprocess"})
    idempotency_mode = IdempotencyMode.NON_IDEMPOTENT

    async def execute(self, code: str, test_framework: str = "pytest", timeout_seconds: int = 300) -> ToolResult:
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "test_script.py"
            script_path.write_text(code, encoding="utf-8")

            if test_framework == "pytest":
                cmd = ["python", "-m", "pytest", str(script_path), "-v", "--tb=short"]
            elif test_framework == "unittest":
                cmd = ["python", "-m", "unittest", str(script_path)]
            else:
                cmd = ["python", str(script_path)]

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
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
                        "script_path": str(script_path),
                    },
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return ToolResult(success=False, error=f"Execution timed out after {timeout_seconds}s")
