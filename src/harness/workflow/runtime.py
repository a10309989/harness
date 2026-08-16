"""Lease-based worker that resumes approved durable workflows."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from harness.workflow.human_tasks import HumanTaskService
from harness.workflow.resumer import ApprovedToolResumer
from harness.workflow.service import WorkflowService

logger = logging.getLogger(__name__)


class ResumeWorker:
    """Legacy polling worker used until Temporal execution is authoritative."""

    def __init__(
        self,
        workflow_service: WorkflowService,
        human_task_service: HumanTaskService,
        agent_registry,
        session_manager,
        *,
        poll_interval: float = 0.2,
        lease_seconds: int = 60,
    ) -> None:
        self.workflow_service = workflow_service
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self.owner = f"resume-worker-{uuid4().hex}"
        self._stopping = asyncio.Event()
        self.resumer = ApprovedToolResumer(
            workflow_service,
            human_task_service,
            agent_registry,
            session_manager,
        )

    async def run(self) -> None:
        while not self._stopping.is_set():
            processed = await self.drain_once()
            if processed == 0:
                try:
                    await asyncio.wait_for(
                        self._stopping.wait(),
                        timeout=self.poll_interval,
                    )
                except asyncio.TimeoutError:
                    pass

    async def stop(self) -> None:
        self._stopping.set()

    async def drain_once(self, limit: int = 20) -> int:
        processed = 0
        for workflow_id in await self.workflow_service.list_resume_requested(limit):
            expires_at = (
                datetime.now(timezone.utc)
                + timedelta(seconds=self.lease_seconds)
            ).isoformat()
            if not await self.workflow_service.acquire_resume_lease(
                workflow_id,
                self.owner,
                expires_at,
            ):
                continue
            try:
                await self._resume(workflow_id)
                processed += 1
            finally:
                await self.workflow_service.release_lease(workflow_id, self.owner)
        return processed

    async def _resume(self, workflow_id: str) -> None:
        try:
            await self.resumer.resume(workflow_id)
        except Exception:
            logger.exception("Workflow resume failed: %s", workflow_id)
