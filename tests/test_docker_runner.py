from pathlib import Path
import os

import httpx
import pytest

from harness.execution.runner import DockerRunner
from harness.execution.runner_manager import (
    RunnerManager,
    RunnerSecurityPolicy,
    RunnerTaskRequest,
    RunnerTaskStore,
    create_runner_manager_app,
)
from harness.models.execution import ExecutionResult, ExecutionStatus


def test_docker_runner_uses_restricted_isolation_profile(tmp_path):
    command = DockerRunner(image="registry.example/runner@sha256:abc").command(
        Path(tmp_path), "harness-run-test"
    )

    assert ["--network", "none"] == command[command.index("--network"):command.index("--network") + 2]
    assert "--read-only" in command
    assert ["--cap-drop", "ALL"] == command[command.index("--cap-drop"):command.index("--cap-drop") + 2]
    assert ["--security-opt", "no-new-privileges"] == command[command.index("--security-opt"):command.index("--security-opt") + 2]
    assert "seccomp=default" not in command
    assert "--privileged" not in command
    assert "registry.example/runner@sha256:abc" in command


def test_runner_manager_requires_digest_allowlist():
    policy = RunnerSecurityPolicy(allowed_image_digests=frozenset({"sha256:abc"}))
    policy.validate_image("registry.example/runner@sha256:abc")
    with pytest.raises(ValueError, match="pinned by digest"):
        policy.validate_image("registry.example/runner:latest")
    with pytest.raises(ValueError, match="allowlisted"):
        policy.validate_image("registry.example/runner@sha256:def")


async def test_runner_manager_rejects_failed_image_scan():
    manager = RunnerManager(
        default_image="registry.example/runner@sha256:abc",
        policy=RunnerSecurityPolicy(allowed_image_digests=frozenset({"sha256:abc"})),
        scanner=lambda image: False,
    )
    with pytest.raises(ValueError, match="scanner"):
        await manager.submit(
            RunnerTaskRequest(
                script_id="script",
                test_case_id="case",
                execution_request_id="request",
                source_code="def test_x(): pass",
            )
        )


async def test_runner_manager_requires_image_signature_when_enabled():
    manager = RunnerManager(
        default_image="registry.example/runner@sha256:abc",
        policy=RunnerSecurityPolicy(
            allowed_image_digests=frozenset({"sha256:abc"}),
            signature_required=True,
        ),
    )
    with pytest.raises(ValueError, match="signature verifier"):
        await manager.submit(
            RunnerTaskRequest(
                script_id="script",
                test_case_id="case",
                execution_request_id="request",
                source_code="def test_x(): pass",
            )
        )


async def test_runner_manager_rejects_failed_image_signature():
    manager = RunnerManager(
        default_image="registry.example/runner@sha256:abc",
        policy=RunnerSecurityPolicy(allowed_image_digests=frozenset({"sha256:abc"})),
        signature_verifier=lambda image: False,
    )
    with pytest.raises(ValueError, match="signature"):
        await manager.submit(
            RunnerTaskRequest(
                script_id="script",
                test_case_id="case",
                execution_request_id="request",
                source_code="def test_x(): pass",
            )
        )


async def test_runner_manager_rpc_requires_token():
    manager = RunnerManager(
        default_image="registry.example/runner@sha256:abc",
        policy=RunnerSecurityPolicy(allowed_image_digests=frozenset({"sha256:abc"})),
    )
    app = create_runner_manager_app(manager, auth_token="secret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://runner") as client:
        response = await client.post(
            "/v1/runs",
            headers={"X-Runner-Token": "wrong"},
            json={
                "script_id": "script",
                "test_case_id": "case",
                "execution_request_id": "request",
                "source_code": "def test_x(): pass",
            },
        )
    assert response.status_code == 401


async def test_runner_task_store_persists_queue_lifecycle(db):
    store = RunnerTaskStore(db)
    request = RunnerTaskRequest(
        script_id="script",
        test_case_id="case",
        execution_request_id="request",
        source_code="def test_x(): pass",
    )

    await store.enqueue("task-1", request, "registry.example/runner@sha256:abc")
    await store.mark_running("task-1")
    await store.mark_completed(
        "task-1",
        ExecutionResult(
            id="result-1",
            script_id="script",
            test_case_id="case",
            status=ExecutionStatus.PASSED,
            stdout="ok",
            duration_seconds=0.1,
            execution_request_id="request",
        ),
    )
    summary = await store.summary()

    assert sum(summary.values()) == 1


@pytest.mark.skipif(
    os.getenv("HARNESS_RUN_DOCKER_INTEGRATION") != "1",
    reason="requires the local harness-runner image and Docker daemon",
)
async def test_docker_runner_executes_pytest_in_isolated_container():
    result = await DockerRunner().execute(
        script_id="runner-smoke",
        test_case_id="runner-smoke",
        execution_request_id="runner-smoke",
        source_code="def test_isolated_runner():\n    assert 2 + 2 == 4\n",
        timeout_seconds=30,
    )

    assert result.status == ExecutionStatus.PASSED
    assert result.exit_code == 0
