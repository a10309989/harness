"""Docker-isolated test script execution."""

from __future__ import annotations

import asyncio
import base64
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from harness.models.execution import ExecutionResult, ExecutionStatus
from harness.utils.id_gen import generate_id

logger = logging.getLogger(__name__)


class DockerRunner:
    """Runs untrusted test code in a short-lived, least-privilege container."""

    def __init__(
        self,
        *,
        image: str = "harness-runner:local",
        docker_binary: str = "docker",
        memory_limit: str = "512m",
        cpu_limit: float = 1.0,
        pids_limit: int = 256,
        seccomp_profile: str = "default",
        apparmor_profile: str | None = None,
        egress_policy: str = "none",
    ) -> None:
        self.image = image
        self.docker_binary = docker_binary
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.pids_limit = pids_limit
        self.seccomp_profile = seccomp_profile
        self.apparmor_profile = apparmor_profile
        self.egress_policy = egress_policy

    def command(
        self,
        workspace: Path,
        container_name: str,
        environment: dict[str, str] | None = None,
    ) -> list[str]:
        """Build the non-negotiable isolation profile for one execution."""
        command = [
            self.docker_binary, "run", "--rm", "--name", container_name,
            "--network", self._network_mode(), "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(self.pids_limit),
            "--memory", self.memory_limit, "--cpus", str(self.cpu_limit),
            "--user", "10001:10001", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--workdir", "/tmp",
            self.image,
            "sh", "-ec",
            "mkdir -p /tmp/screenshots; printf '%s' \"$HARNESS_RUNNER_SOURCE_B64\" | base64 -d > /tmp/test_script.py; "
            "set +e; python -m pytest /tmp/test_script.py -v -s --tb=short -o cache_dir=/tmp/.pytest_cache; status=$?; "
            "for file in /tmp/screenshots/*.png; do [ -e \"$file\" ] || continue; "
            "printf '\\n[HARNESS_SCREENSHOT] %s:%s\\n' \"$(basename \"$file\")\" \"$(base64 -w0 \"$file\")\"; done; exit $status",
        ]
        if self.egress_policy == "default":
            image_index = command.index(self.image)
            command[image_index:image_index] = [
                "--add-host",
                "host.docker.internal:host-gateway",
            ]
        for name, value in sorted((environment or {}).items()):
            image_index = command.index(self.image)
            command[image_index:image_index] = ["--env", f"{name}={value}"]
        if self.seccomp_profile and self.seccomp_profile != "default":
            image_index = command.index(self.image)
            command[image_index:image_index] = [
                "--security-opt",
                f"seccomp={self.seccomp_profile}",
            ]
        if self.apparmor_profile:
            image_index = command.index(self.image)
            command[image_index:image_index] = [
                "--security-opt",
                f"apparmor={self.apparmor_profile}",
            ]
        return command

    def _network_mode(self) -> str:
        if self.egress_policy == "none":
            return "none"
        if self.egress_policy == "default":
            return "bridge"
        raise ValueError(f"Unsupported runner egress policy: {self.egress_policy}")

    async def execute(
        self,
        script_id: str,
        test_case_id: str,
        source_code: str,
        execution_request_id: str,
        timeout_seconds: int = 3600,
        environment: dict[str, str] | None = None,
    ) -> ExecutionResult:
        result = ExecutionResult(
            id=generate_id(), execution_request_id=execution_request_id,
            script_id=script_id, test_case_id=test_case_id, status=ExecutionStatus.RUNNING,
        )
        started = datetime.now(timezone.utc)
        result.start_time = started
        container_name = f"harness-run-{result.id.lower()}"
        with tempfile.TemporaryDirectory(prefix="harness-run-") as directory:
            Path(directory, "test_script.py").write_text(source_code, encoding="utf-8")
            process = None
            try:
                runner_environment = dict(environment or {})
                runner_environment["HARNESS_RUNNER_SOURCE_B64"] = base64.b64encode(
                    source_code.encode("utf-8")
                ).decode("ascii")
                runner_environment["HARNESS_UI_SHOT_DIR"] = "/tmp/screenshots"
                process = await asyncio.create_subprocess_exec(
                    *self.command(Path(directory), container_name, runner_environment),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout_seconds
                )
                result.exit_code = process.returncode
                result.stdout = stdout.decode("utf-8", errors="replace")
                result.stderr = stderr.decode("utf-8", errors="replace")
                result.artifacts = self._extract_screenshots(result.stdout)
                result.status = (
                    ExecutionStatus.PASSED if process.returncode == 0 else ExecutionStatus.FAILED
                )
            except asyncio.TimeoutError:
                await self._remove_container(container_name)
                result.status = ExecutionStatus.TIMED_OUT
                result.stderr = f"Execution timed out after {timeout_seconds}s"
            except Exception as exc:
                result.status = ExecutionStatus.ERROR
                result.stderr = str(exc)
                logger.exception("Docker execution failed: %s", result.id)
            finally:
                if process and process.returncode is None:
                    process.kill()
                    await process.communicate()
                ended = datetime.now(timezone.utc)
                result.end_time = ended
                result.duration_seconds = (ended - started).total_seconds()
        return result

    @staticmethod
    def _extract_screenshots(stdout: str) -> dict[str, str]:
        evidence: dict[str, str] = {}
        for line in stdout.splitlines():
            if not line.startswith("[HARNESS_SCREENSHOT] "):
                continue
            name, separator, encoded = line.removeprefix("[HARNESS_SCREENSHOT] ").partition(":")
            if separator and name and encoded:
                evidence[f"screenshot:{name}"] = f"data:image/png;base64,{encoded}"
        return evidence

    async def _remove_container(self, container_name: str) -> None:
        process = await asyncio.create_subprocess_exec(
            self.docker_binary, "rm", "--force", container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.communicate()


class ScriptRunner:
    """Worker-side runner facade; production deployments should use RunnerClient."""

    def __new__(cls, *args, **kwargs):
        from harness.execution.runner_manager import RunnerClient

        return RunnerClient(*args, **kwargs)
