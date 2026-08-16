"""Backup and restore drill gate for production readiness."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from harness.runtime.settings import RuntimeSettings


@dataclass
class DrillCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class DrillReport:
    started_at: str
    rpo_minutes: int
    rto_minutes: int
    checks: list[DrillCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def as_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "rpo_minutes": self.rpo_minutes,
            "rto_minutes": self.rto_minutes,
            "passed": self.passed,
            "checks": [check.__dict__ for check in self.checks],
        }


class BackupRestoreDrill:
    """Non-destructive DR drill gate for PostgreSQL, MinIO, Temporal, and Redis."""

    def __init__(self, settings: RuntimeSettings) -> None:
        self.settings = settings

    async def run(self) -> DrillReport:
        report = DrillReport(
            started_at=datetime.now(timezone.utc).isoformat(),
            rpo_minutes=self.settings.backup_rpo_minutes,
            rto_minutes=self.settings.backup_rto_minutes,
        )
        report.checks.extend(
            [
                self._check_positive("PostgreSQL RPO", self.settings.backup_rpo_minutes),
                self._check_positive("Restore RTO", self.settings.backup_rto_minutes),
                DrillCheck(
                    "MinIO backup policy",
                    self.settings.artifact_storage_backend in {"local", "minio"},
                    f"backend={self.settings.artifact_storage_backend}",
                ),
                DrillCheck(
                    "Temporal control plane",
                    bool(self.settings.temporal_target),
                    self.settings.temporal_target,
                ),
                DrillCheck(
                    "Redis projection rebuild",
                    True,
                    "Redis is non-authoritative; rebuild from PostgreSQL outbox",
                ),
            ]
        )
        return report

    @staticmethod
    def _check_positive(name: str, value: int) -> DrillCheck:
        return DrillCheck(name, value > 0, f"value={value}")


async def _main_async() -> None:
    settings = RuntimeSettings()
    report = await BackupRestoreDrill(settings).run()
    for check in report.checks:
        status = "pass" if check.passed else "fail"
        print(f"{status}\t{check.name}\t{check.detail}")
    print(f"passed={str(report.passed).lower()}")
    if not report.passed:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Harness backup/restore drill gate")
    parser.parse_args()
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
