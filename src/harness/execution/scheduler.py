"""Scheduler — cron-style and event-driven test scheduling."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from harness.models.execution import ExecutionRequest, ExecutionStatus

logger = logging.getLogger(__name__)


class Scheduler:
    """Schedules test executions.

    Supports:
    - Immediate execution (fire-and-forget)
    - Cron-based recurring execution
    - Event-driven execution (webhook-triggered)

    Note: Full cron/event scheduling is placeholder. In production,
    this would integrate with APScheduler or Celery Beat.
    """

    _pending: list[tuple[ExecutionRequest, Any]]  # (request, callback)
    _running: dict[str, Any]

    def __init__(self) -> None:
        self._pending = []
        self._running = {}

    async def schedule(self, request: ExecutionRequest, callback) -> str:
        """Schedule an execution request.

        Args:
            request: The execution request.
            callback: Async callable to invoke when scheduled.

        Returns:
            The request ID.
        """
        if request.schedule_type == "immediate":
            asyncio.create_task(self._execute_now(request, callback))
        elif request.schedule_type == "cron":
            self._pending.append((request, callback))
            logger.info(f"Scheduled cron execution: {request.id} ({request.cron_expression})")
        else:
            logger.info(f"Scheduled event-driven execution: {request.id} (trigger: {request.trigger_event})")

        return request.id

    async def _execute_now(self, request: ExecutionRequest, callback) -> None:
        """Execute a request immediately.

        Args:
            request: The execution request.
            callback: Async callable to execute.
        """
        logger.info(f"Executing immediately: {request.id}")
        try:
            await callback(request)
        except Exception as e:
            logger.error(f"Execution failed: {request.id} — {e}")

    async def trigger_event(self, event_name: str) -> list[str]:
        """Trigger event-driven executions.

        Args:
            event_name: The triggering event.

        Returns:
            List of triggered execution IDs.
        """
        triggered = []
        remaining = []
        for request, callback in self._pending:
            if request.trigger_event == event_name:
                asyncio.create_task(self._execute_now(request, callback))
                triggered.append(request.id)
            else:
                remaining.append((request, callback))
        self._pending = remaining
        return triggered

    def get_status(self, request_id: str) -> str:
        """Get the status of a scheduled execution.

        Args:
            request_id: The request ID.

        Returns:
            Status string.
        """
        if request_id in self._running:
            return "running"
        for req, _ in self._pending:
            if req.id == request_id:
                return "pending"
        return "unknown"
