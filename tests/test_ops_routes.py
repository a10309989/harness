from types import SimpleNamespace

import pytest

from harness.api.routes.ops import _counts, operations_summary
from harness.runtime.settings import RuntimeSettings


@pytest.mark.asyncio
async def test_operations_summary_includes_operational_planes(db):
    settings = RuntimeSettings(redis_events_enabled=True, runner_signature_required=True)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(database=db, settings=settings)))

    summary = await operations_summary(request, SimpleNamespace())

    assert set(summary) == {"outbox", "runner", "redis", "runner_manager", "backup", "otel"}
    assert summary["redis"]["enabled"] is True
    assert summary["runner_manager"]["max_concurrent_tasks"] == settings.runner_max_concurrent_tasks


@pytest.mark.asyncio
async def test_counts_returns_empty_for_missing_table(db):
    assert await _counts(db, "missing_table", "status") == {}
