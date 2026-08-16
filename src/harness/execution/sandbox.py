"""Sandbox — isolation strategies for test execution."""

import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class Sandbox:
    """Provides isolated execution environments for test scripts.

    Supports:
    - Temporary venv: Creates a fresh virtualenv for each run
    - Directory isolation: Each execution gets its own temp directory
    - Dependency installation: Auto-installs required packages
    """

    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else Path(tempfile.gettempdir()) / "harness_sandboxes"

    def create(self, execution_id: str) -> Path:
        """Create an isolated execution directory.

        Args:
            execution_id: Unique execution identifier.

        Returns:
            Path to the sandbox directory.
        """
        sandbox_path = self.base_dir / execution_id
        sandbox_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Sandbox created: {sandbox_path}")
        return sandbox_path

    def cleanup(self, sandbox_path: Path) -> None:
        """Clean up a sandbox directory.

        Args:
            sandbox_path: Path to remove.
        """
        import shutil
        if sandbox_path.exists():
            shutil.rmtree(sandbox_path, ignore_errors=True)
            logger.info(f"Sandbox cleaned: {sandbox_path}")

    async def setup_dependencies(self, sandbox_path: Path, dependencies: list[str]) -> bool:
        """Install required dependencies into the sandbox.

        Args:
            sandbox_path: Sandbox directory.
            dependencies: List of pip package specs.

        Returns:
            True if all dependencies were installed successfully.
        """
        if not dependencies:
            return True

        import asyncio
        for dep in dependencies:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "pip", "install", dep, "--target", str(sandbox_path / "lib"),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                if proc.returncode != 0:
                    logger.warning(f"Failed to install dependency: {dep}")
                    return False
            except Exception as e:
                logger.error(f"Dependency installation error: {e}")
                return False
        return True
