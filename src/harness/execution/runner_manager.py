"""Dedicated Runner Manager RPC boundary for Docker-isolated execution."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from harness.execution.runner import DockerRunner
from harness.models.execution import ExecutionResult
from harness.observability.context import new_id
from harness.observability.otel import traced_span

ImageScanner = Callable[[str], bool | Awaitable[bool]]
ImageSignatureVerifier = Callable[[str], bool | Awaitable[bool]]


class RunnerTaskRequest(BaseModel):
    script_id: str
    test_case_id: str
    source_code: str
    execution_request_id: str
    timeout_seconds: int = Field(default=3600, ge=1, le=7200)
    image: str | None = None
    environment: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True)
class RunnerTaskRecord:
    id: str
    status: str
    image: str
    script_id: str
    test_case_id: str
    execution_request_id: str


@dataclass(frozen=True)
class RunnerSecurityPolicy:
    """Non-negotiable admission policy enforced inside Runner Manager."""

    allowed_image_digests: frozenset[str]
    max_concurrent_tasks: int = 2
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    pids_limit: int = 256
    seccomp_profile: str = "default"
    apparmor_profile: str | None = None
    egress_policy: str = "none"
    signature_required: bool = False

    def validate_image(self, image: str) -> None:
        # Local development uses a deliberately named image built from the
        # checked-in Runner Dockerfile. Production must still use a digest.
        if image == "harness-runner:local" and "local-dev" in self.allowed_image_digests:
            return
        if "@sha256:" not in image:
            raise ValueError("Runner image must be pinned by digest")
        digest = image.rsplit("@", 1)[1]
        if digest not in self.allowed_image_digests:
            raise ValueError("Runner image digest is not allowlisted")


class RunnerManager:
    """Owns Docker access and exposes a narrow authenticated execution RPC."""

    def __init__(
        self,
        *,
        default_image: str,
        policy: RunnerSecurityPolicy,
        scanner: ImageScanner | None = None,
        signature_verifier: ImageSignatureVerifier | None = None,
        docker_binary: str = "docker",
        store: "RunnerTaskStore | None" = None,
    ) -> None:
        self.default_image = default_image
        self.policy = policy
        self.scanner = scanner
        self.signature_verifier = signature_verifier
        self.docker_binary = docker_binary
        self.store = store
        self._semaphore = asyncio.Semaphore(policy.max_concurrent_tasks)

    async def submit(self, request: RunnerTaskRequest) -> ExecutionResult:
        image = request.image or self.default_image
        with traced_span(
            "runner.submit",
            image=image,
            script_id=request.script_id,
            execution_request_id=request.execution_request_id,
        ):
            self.policy.validate_image(image)
            await self._scan(image)
            await self._verify_signature(image)
            task_id = new_id()
            if self.store is not None:
                await self.store.enqueue(task_id, request, image)
            async with self._semaphore:
                if self.store is not None:
                    await self.store.mark_running(task_id)
                runner = DockerRunner(
                    image=image,
                    docker_binary=self.docker_binary,
                    memory_limit=self.policy.memory_limit,
                    cpu_limit=self.policy.cpu_limit,
                    pids_limit=self.policy.pids_limit,
                    seccomp_profile=self.policy.seccomp_profile,
                    apparmor_profile=self.policy.apparmor_profile,
                    egress_policy=self.policy.egress_policy,
                )
                try:
                    result = await runner.execute(
                        script_id=request.script_id,
                        test_case_id=request.test_case_id,
                        source_code=request.source_code,
                        execution_request_id=request.execution_request_id,
                        timeout_seconds=request.timeout_seconds,
                        environment=self._sanitize_environment(request.environment),
                    )
                    if self.store is not None:
                        await self.store.mark_completed(task_id, result)
                    return result
                except Exception as exc:
                    if self.store is not None:
                        await self.store.mark_failed(task_id, str(exc))
                    raise

    async def _scan(self, image: str) -> None:
        if self.scanner is None:
            return
        result = self.scanner(image)
        allowed = await result if hasattr(result, "__await__") else result
        if not allowed:
            raise ValueError("Runner image failed scanner policy")

    def _sanitize_environment(self, environment: dict[str, str]) -> dict[str, str]:
        allowed_names = {"HARNESS_UI_TARGET_URL", "PLAYWRIGHT_BROWSERS_PATH"}
        sanitized: dict[str, str] = {}
        for name, value in environment.items():
            if name not in allowed_names:
                continue
            sanitized[name] = str(value)[:500]
        return sanitized

    async def _verify_signature(self, image: str) -> None:
        if self.signature_verifier is None:
            if self.policy.signature_required:
                raise ValueError("Runner image signature verifier is required")
            return
        result = self.signature_verifier(image)
        allowed = await result if hasattr(result, "__await__") else result
        if not allowed:
            raise ValueError("Runner image signature verification failed")


class RunnerTaskStore:
    """Persistent runner task queue and execution ledger."""

    def __init__(self, db) -> None:
        self.db = db

    async def enqueue(self, task_id: str, request: RunnerTaskRequest, image: str) -> None:
        now = self._now()
        await self.db.execute(
            """INSERT INTO runner_tasks
                   (id, status, image, script_id, test_case_id, execution_request_id,
                    timeout_seconds, created_at)
                   VALUES ($1, 'queued', $2, $3, $4, $5, $6, $7)""",
            (
                task_id,
                image,
                request.script_id,
                request.test_case_id,
                request.execution_request_id,
                request.timeout_seconds,
                now,
            ),
        )
        await self.db.commit()

    async def mark_running(self, task_id: str) -> None:
        await self.db.execute(
            "UPDATE runner_tasks SET status = 'running', started_at = $1 WHERE id = $2",
            (self._now(), task_id),
        )
        await self.db.commit()

    async def mark_completed(self, task_id: str, result: ExecutionResult) -> None:
        await self.db.execute(
            """UPDATE runner_tasks
                   SET status = $1, result_json = $2, completed_at = $3
                   WHERE id = $4""",
            (
                str(result.status),
                result.model_dump_json(),
                self._now(),
                task_id,
            ),
        )
        await self.db.commit()

    async def mark_failed(self, task_id: str, error: str) -> None:
        await self.db.execute(
            """UPDATE runner_tasks
                   SET status = 'error', error = $1, completed_at = $2
                   WHERE id = $3""",
            (error[:1000], self._now(), task_id),
        )
        await self.db.commit()

    async def summary(self) -> dict[str, int]:
        rows = await self.db.fetch_all(
            "SELECT status, COUNT(*) AS count FROM runner_tasks GROUP BY status"
        )
        return {row["status"]: row["count"] for row in rows}

    def _now(self):
        return datetime.now(timezone.utc).replace(tzinfo=None)


class RunnerClient:
    """Worker-side RPC client; it never shells out to Docker."""

    def __init__(
        self,
        *,
        endpoint: str = "http://127.0.0.1:8099",
        token: str = "",
        timeout_seconds: float = 3700,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    async def execute(
        self,
        script_id: str,
        test_case_id: str,
        source_code: str,
        execution_request_id: str,
        timeout_seconds: int = 3600,
        environment: dict[str, str] | None = None,
    ) -> ExecutionResult:
        request = RunnerTaskRequest(
            script_id=script_id,
            test_case_id=test_case_id,
            source_code=source_code,
            execution_request_id=execution_request_id,
            timeout_seconds=timeout_seconds,
            environment=environment or {},
        )
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.endpoint}/v1/runs",
                headers={"X-Runner-Token": self.token},
                json=request.model_dump(),
            )
            response.raise_for_status()
            return ExecutionResult.model_validate(response.json())


def create_runner_manager_app(
    manager: RunnerManager,
    *,
    auth_token: str,
) -> FastAPI:
    app = FastAPI(title="Harness Runner Manager")

    @app.post("/v1/runs")
    async def submit_run(
        request: RunnerTaskRequest,
        x_runner_token: str = Header(default=""),
    ) -> dict[str, Any]:
        if not auth_token or x_runner_token != auth_token:
            raise HTTPException(status_code=401, detail="Invalid runner token")
        try:
            result = await manager.submit(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    return app
