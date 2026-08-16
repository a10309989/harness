"""Persisted, reproducible evaluation suite service."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from harness.observability.context import get_execution_context


class EvaluationService:
    """Stores versioned-by-snapshot evaluation and regression runs."""

    def __init__(self, db, audit_service) -> None:
        self.db = db
        self.audit_service = audit_service

    async def create_suite(
        self, *, name: str, category: str, cases: list[dict], pass_threshold: float
    ) -> dict:
        case_ids = [case.get("id") for case in cases]
        if (
            not name.strip()
            or not cases
            or not 0 < pass_threshold <= 1
            or any(not case_id for case_id in case_ids)
            or len(set(case_ids)) != len(case_ids)
        ):
            raise ValueError("name, cases, and pass_threshold must be valid")
        context = get_execution_context()
        now = self._now()
        suite = {
            "id": str(uuid4()),
            "name": name.strip(),
            "category": category,
            "cases": cases,
            "pass_threshold": pass_threshold,
        }
        await self.db.execute(
            """INSERT INTO evaluation_suites
                   (id, tenant_id, name, category, cases_json, pass_threshold, created_by, created_at, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
            (suite["id"], context.actor.tenant_id, suite["name"], category,
             json.dumps(cases, sort_keys=True), pass_threshold, context.actor.actor_id, now, now),
        )
        await self.db.commit()
        await self.audit_service.record("evaluation.suite_created", resource_type="evaluation_suite", resource_id=suite["id"])
        return suite

    async def start_run(self, suite_id: str) -> dict:
        context = get_execution_context()
        suite = await self._suite(suite_id, context.actor.tenant_id)
        if suite is None:
            raise KeyError(suite_id)
        run = {"id": str(uuid4()), "suite_id": suite_id, "status": "running"}
        await self.db.execute(
            """INSERT INTO evaluation_runs
                   (id, suite_id, tenant_id, trace_id, status, suite_snapshot_json, created_by, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            (run["id"], suite_id, context.actor.tenant_id, context.trace_id, "running",
             json.dumps(suite, sort_keys=True), context.actor.actor_id, self._now()),
        )
        await self.db.commit()
        await self.audit_service.record("evaluation.run_started", resource_type="evaluation_run", resource_id=run["id"])
        return run

    async def record_results(self, run_id: str, results: list[dict]) -> dict:
        context = get_execution_context()
        run = await self._run(run_id, context.actor.tenant_id)
        if run is None or run["status"] != "running":
            raise ValueError("evaluation run is not running")
        cases = {case["id"] for case in run["suite_snapshot"]["cases"]}
        result_case_ids = [result.get("case_id") for result in results]
        if set(result_case_ids) != cases or len(result_case_ids) != len(cases):
            raise ValueError("results must contain exactly one entry for each suite case")
        now = self._now()
        for result in results:
            if result.get("status") not in {"passed", "failed", "error", "skipped"}:
                raise ValueError("invalid evaluation result status")
            await self.db.execute(
                """INSERT INTO evaluation_results (id, run_id, case_id, status, score, output_json, created_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                (str(uuid4()), run_id, result["case_id"], result["status"], result.get("score"),
                 json.dumps(result.get("output", {}), sort_keys=True), now),
            )
        passed = sum(item["status"] == "passed" for item in results)
        summary = {"total": len(results), "passed": passed, "pass_rate": passed / len(results),
                   "passed_threshold": passed / len(results) >= run["suite_snapshot"]["pass_threshold"]}
        status = "passed" if summary["passed_threshold"] else "failed"
        await self.db.execute(
            "UPDATE evaluation_runs SET status = $1, summary_json = $2, completed_at = $3 WHERE id = $4",
            (status, json.dumps(summary, sort_keys=True), now, run_id),
        )
        await self.db.commit()
        await self.audit_service.record("evaluation.run_completed", resource_type="evaluation_run", resource_id=run_id, decision="success" if summary["passed_threshold"] else "failure", metadata=summary)
        return {"id": run_id, "status": status, "summary": summary}

    async def _suite(self, suite_id: str, tenant_id: str) -> dict | None:
        row = await self.db.fetch_one(
            "SELECT * FROM evaluation_suites WHERE id = $1 AND tenant_id = $2",
            (suite_id, tenant_id),
        )
        return self._decode_suite(row) if row else None

    async def _run(self, run_id: str, tenant_id: str) -> dict | None:
        row = await self.db.fetch_one(
            "SELECT * FROM evaluation_runs WHERE id = $1 AND tenant_id = $2",
            (run_id, tenant_id),
        )
        return {**dict(row), "suite_snapshot": json.loads(row["suite_snapshot_json"])} if row else None

    @staticmethod
    def _decode_suite(row) -> dict:
        return {**dict(row), "cases": json.loads(row["cases_json"])}

    def _now(self):
        return datetime.now(timezone.utc).replace(tzinfo=None)
