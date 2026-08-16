"""Reporter — result aggregation and test report generation."""

from harness.models.execution import ExecutionResult, ExecutionStatus, TestRun
from harness.utils.id_gen import generate_id


class Reporter:
    """Aggregates execution results and generates test run reports."""

    async def create_run(self, name: str, execution_request_id: str) -> TestRun:
        """Create a new test run container.

        Args:
            name: Run name.
            execution_request_id: Associated execution request ID.

        Returns:
            A new TestRun.
        """
        return TestRun(
            id=generate_id(),
            name=name,
            execution_request_id=execution_request_id,
        )

    async def add_result(self, run: TestRun, result: ExecutionResult) -> None:
        """Add a result to a test run.

        Args:
            run: The test run.
            result: The execution result to add.
        """
        run.results.append(result)
        self._update_summary(run)

    def _update_summary(self, run: TestRun) -> None:
        """Update the aggregated summary statistics.

        Args:
            run: The test run to update.
        """
        total = len(run.results)
        passed = sum(1 for r in run.results if r.status == ExecutionStatus.PASSED)
        failed = sum(1 for r in run.results if r.status == ExecutionStatus.FAILED)
        errored = sum(1 for r in run.results if r.status == ExecutionStatus.ERROR)
        skipped = sum(1 for r in run.results if r.status == ExecutionStatus.SKIPPED)
        timed_out = sum(1 for r in run.results if r.status == ExecutionStatus.TIMED_OUT)

        total_duration = sum(r.duration_seconds or 0 for r in run.results)

        run.summary = {
            "total": total,
            "passed": passed,
            "failed": failed,
            "errored": errored,
            "skipped": skipped,
            "timed_out": timed_out,
            "pass_rate": round(passed / total * 100, 1) if total > 0 else 0,
            "total_duration_seconds": round(total_duration, 2),
        }

    def generate_report(self, run: TestRun) -> str:
        """Generate a human-readable report.

        Args:
            run: The test run.

        Returns:
            Formatted report string.
        """
        s = run.summary
        lines = [
            f"Test Run: {run.name}",
            f"ID: {run.id}",
            f"Results: {s.get('total', 0)} total",
            f"  ✅ Passed:  {s.get('passed', 0)}",
            f"  ❌ Failed:  {s.get('failed', 0)}",
            f"  ⚠️  Errored: {s.get('errored', 0)}",
            f"  ⏭️  Skipped: {s.get('skipped', 0)}",
            f"  ⏱️  Timed out: {s.get('timed_out', 0)}",
            f"  📊 Pass Rate: {s.get('pass_rate', 0)}%",
            f"  ⏱️  Duration: {s.get('total_duration_seconds', 0)}s",
        ]
        return "\n".join(lines)
