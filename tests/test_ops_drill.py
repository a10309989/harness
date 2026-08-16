import pytest

from harness.ops.drill import BackupRestoreDrill
from harness.runtime.settings import RuntimeSettings


@pytest.mark.asyncio
async def test_backup_restore_drill_passes_with_default_thresholds():
    settings = RuntimeSettings()

    report = await BackupRestoreDrill(settings).run()

    assert report.passed is True
    assert {check.name for check in report.checks} >= {
        "PostgreSQL RPO",
        "Restore RTO",
        "MinIO backup policy",
        "Temporal control plane",
        "Redis projection rebuild",
    }


@pytest.mark.asyncio
async def test_backup_restore_drill_fails_invalid_rpo():
    settings = RuntimeSettings(backup_rpo_minutes=0)

    report = await BackupRestoreDrill(settings).run()

    assert report.passed is False
    assert any(check.name == "PostgreSQL RPO" and not check.passed for check in report.checks)
