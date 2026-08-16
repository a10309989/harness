"""Runner Manager service entrypoint."""

from __future__ import annotations

import uvicorn

from harness.execution.runner_manager import (
    RunnerManager,
    RunnerSecurityPolicy,
    RunnerTaskStore,
    create_runner_manager_app,
)
from harness.db import create_database
from harness.runtime.settings import RuntimeSettings


async def build_app():
    settings = RuntimeSettings()
    db = create_database(settings.database_url)
    await db.initialize()
    allowed_digests = frozenset(
        digest.strip()
        for digest in settings.runner_allowed_image_digests.split(",")
        if digest.strip()
    )
    manager = RunnerManager(
        default_image=settings.runner_image,
        policy=RunnerSecurityPolicy(
            allowed_image_digests=allowed_digests,
            max_concurrent_tasks=settings.runner_max_concurrent_tasks,
            memory_limit=settings.runner_memory_limit,
            cpu_limit=settings.runner_cpu_limit,
            pids_limit=settings.runner_pids_limit,
            seccomp_profile=settings.runner_seccomp_profile,
            apparmor_profile=settings.runner_apparmor_profile or None,
            egress_policy=settings.runner_egress_policy,
            signature_required=settings.runner_signature_required,
        ),
        docker_binary=settings.runner_docker_binary,
        store=RunnerTaskStore(db),
    )
    app = create_runner_manager_app(manager, auth_token=settings.runner_rpc_token)
    app.state.database = db

    @app.on_event("shutdown")
    async def close_database() -> None:
        await db.close()

    return app


app = None


def main() -> None:
    settings = RuntimeSettings()
    uvicorn.run(
        "harness.execution.runner_manager_app:create_app_sync",
        factory=True,
        host=settings.runner_manager_host,
        port=settings.runner_manager_port,
        reload=False,
    )


def create_app_sync():
    settings = RuntimeSettings()
    allowed_digests = frozenset(
        digest.strip()
        for digest in settings.runner_allowed_image_digests.split(",")
        if digest.strip()
    )
    manager = RunnerManager(
        default_image=settings.runner_image,
        policy=RunnerSecurityPolicy(
            allowed_image_digests=allowed_digests,
            max_concurrent_tasks=settings.runner_max_concurrent_tasks,
            memory_limit=settings.runner_memory_limit,
            cpu_limit=settings.runner_cpu_limit,
            pids_limit=settings.runner_pids_limit,
            seccomp_profile=settings.runner_seccomp_profile,
            apparmor_profile=settings.runner_apparmor_profile or None,
            egress_policy=settings.runner_egress_policy,
            signature_required=settings.runner_signature_required,
        ),
        docker_binary=settings.runner_docker_binary,
        store=None,
    )
    return create_runner_manager_app(manager, auth_token=settings.runner_rpc_token)


if __name__ == "__main__":
    main()
