"""Full-chain trace search and span-tree reconstruction.

Traces are reconstructed from the durable ``audit_events`` table, which already
records ``trace_id`` / ``span_id`` / ``parent_span_id`` / ``workflow_id`` /
``session_id`` for every audited step. This service turns those flat, append-only
rows into:

- a searchable list of traces (grouped by ``trace_id``, with timing + status), and
- a hierarchical span tree for a single trace (``parent_span_id`` -> children),
  so the full chain from HTTP request -> workflow -> agent lifecycle -> LLM /
  tool calls can be inspected end to end.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from harness.db.protocols import DatabaseProtocol
from harness.observability.context import get_execution_context

# Columns that contribute to a trace timeline and are cheap to aggregate.
_TRACE_AGG = [
    "workflow_id",
    "session_id",
    "resource_type",
    "resource_id",
]


def _iso_ts(value: Any) -> str:
    if isinstance(value, datetime):
        src = value
        if src.tzinfo is None:
            src = src.replace(tzinfo=timezone.utc)
        else:
            src = src.astimezone(timezone.utc)
        return src.isoformat()
    return str(value) if value is not None else None


def _row_to_dict(row: Any) -> dict[str, Any]:
    result = dict(row)
    if result.get("metadata"):
        try:
            result["metadata"] = json.loads(result["metadata"])
        except (json.JSONDecodeError, TypeError):
            result["metadata"] = {}
    else:
        result["metadata"] = {}
    result["created_at"] = _iso_ts(result.get("created_at"))
    return result


def _duration_ms(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
    except ValueError:
        return None
    return round((e - s).total_seconds() * 1000, 2)


def _parse_ts_param(value: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a tz-aware datetime for query params."""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


class TraceService:
    """Read-only full-chain trace search and reconstruction over audit events."""

    def __init__(self, db: DatabaseProtocol) -> None:
        self.db = db

    async def list_traces(
        self,
        *,
        session_id: str | None = None,
        workflow_id: str | None = None,
        event_type: str | None = None,
        status: str | None = None,
        agent_id: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search traces by session / flow(workflow) / agent(task) / time / status.

        Each summary groups all audit events sharing a ``trace_id`` and carries
        the trace's start/end time, duration, event count and last status.
        """
        tenant = get_execution_context().actor.tenant_id
        clauses = ["tenant_id = $1"]
        params: list[Any] = [tenant]

        filters = {
            "session_id": session_id,
            "workflow_id": workflow_id,
            "event_type": event_type,
            "decision": status,
        }
        for column, value in filters.items():
            if value is not None:
                clauses.append(f"{column} = ${len(params) + 1}")
                params.append(value)
        if agent_id:
            clauses.append(f"resource_id = ${len(params) + 1}")
            params.append(agent_id)
        if from_time:
            clauses.append(f"created_at >= ${len(params) + 1}")
            params.append(_parse_ts_param(from_time))
        if to_time:
            clauses.append(f"created_at <= ${len(params) + 1}")
            params.append(_parse_ts_param(to_time))

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        ordered_last = "array_agg(decision ORDER BY created_at DESC, id DESC)"
        ordered_last_event = "array_agg(event_type ORDER BY created_at DESC, id DESC)"
        select_cols = ", ".join(f"MAX({col}) AS {col}" for col in _TRACE_AGG)

        params.extend([int(limit), int(offset)])
        rows = await self.db.fetch_all(
            f"""SELECT trace_id,
                       MIN(created_at)                             AS start_time,
                       MAX(created_at)                             AS end_time,
                       COUNT(*)                                    AS event_count,
                       ({ordered_last})[1]                         AS status,
                       ({ordered_last_event})[1]                   AS last_event,
                       {select_cols}
                FROM audit_events
                {where}
                GROUP BY trace_id
                ORDER BY start_time DESC
                LIMIT ${len(params) - 1} OFFSET ${len(params)}""",
            tuple(params),
        )

        traces = []
        for row in rows:
            start_time = _iso_ts(row["start_time"])
            end_time = _iso_ts(row["end_time"])
            traces.append(
                {
                    "trace_id": row["trace_id"],
                    "start_time": start_time,
                    "end_time": end_time,
                    "duration_ms": _duration_ms(start_time, end_time),
                    "event_count": row["event_count"],
                    "status": row["status"],
                    "last_event": row["last_event"],
                    "workflow_id": row["workflow_id"],
                    "session_id": row["session_id"],
                    "resource_type": row["resource_type"],
                    "resource_id": row["resource_id"],
                }
            )
        return {"traces": traces, "count": len(traces), "limit": limit, "offset": offset}

    async def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        """Return the full span tree for a single trace.

        Builds a parent/child hierarchy from ``parent_span_id`` and annotates
        every span with its events, start/end time, duration and final status.
        """
        tenant = get_execution_context().actor.tenant_id
        rows = await self.db.fetch_all(
            """SELECT * FROM audit_events
               WHERE trace_id = $1 AND tenant_id = $2
               ORDER BY created_at ASC, id ASC""",
            (trace_id, tenant),
        )
        if not rows:
            return None

        events = [_row_to_dict(row) for row in rows]

        # Flatten into spans keyed by span_id.
        spans: dict[str, dict[str, Any]] = {}
        for event in events:
            span_id = event.get("span_id") or event.get("id")
            span = spans.setdefault(
                span_id,
                {
                    "span_id": span_id,
                    "parent_span_id": event.get("parent_span_id"),
                    "start_time": event["created_at"],
                    "end_time": event["created_at"],
                    "event_types": [],
                    "resource_types": set(),
                    "resource_ids": set(),
                    "workflow_ids": set(),
                    "session_ids": set(),
                    "events": [],
                },
            )
            span["events"].append(event)
            span["event_types"].append(event["event_type"])
            if event["created_at"] > span["end_time"]:
                span["end_time"] = event["created_at"]
            if event.get("resource_type"):
                span["resource_types"].add(event["resource_type"])
            if event.get("resource_id"):
                span["resource_ids"].add(event["resource_id"])
            if event.get("workflow_id"):
                span["workflow_ids"].add(event["workflow_id"])
            if event.get("session_id"):
                span["session_ids"].add(event["session_id"])

        nodes: dict[str, dict[str, Any]] = {}
        for span_id, span in spans.items():
            events_in_span = span["events"]
            last = events_in_span[-1]
            nodes[span_id] = {
                "span_id": span_id,
                "parent_span_id": span["parent_span_id"],
                "start_time": span["start_time"],
                "end_time": span["end_time"],
                "duration_ms": _duration_ms(span["start_time"], span["end_time"]),
                "event_types": span["event_types"],
                "status": last.get("decision") or last.get("event_type"),
                "last_event": last["event_type"],
                "resource_type": sorted(span["resource_types"]),
                "resource_id": sorted(span["resource_ids"]),
                "workflow_ids": sorted(span["workflow_ids"]),
                "session_ids": sorted(span["session_ids"]),
                "children": [],
            }

        # Attach children to parents; spans whose parent is missing become roots.
        roots: list[dict[str, Any]] = []
        for span_id, node in nodes.items():
            parent_id = node["parent_span_id"]
            if parent_id and parent_id in nodes:
                nodes[parent_id]["children"].append(node)
            else:
                roots.append(node)

        first = events[0]
        last = events[-1]
        result = {
            "trace_id": trace_id,
            "start_time": first["created_at"],
            "end_time": last["created_at"],
            "duration_ms": _duration_ms(first["created_at"], last["created_at"]),
            "status": last.get("decision") or last.get("event_type"),
            "workflow_id": first.get("workflow_id"),
            "session_id": first.get("session_id"),
            "event_count": len(events),
            "span_count": len(nodes),
            "root_spans": roots,
            "events": events,
        }
        return result

    async def get_flow(self, workflow_id: str) -> dict[str, Any] | None:
        """Return an end-to-end flow view for one workflow (a full pipeline run).

        Groups every audit event of the workflow into per-stage buckets keyed by
        the pipeline node, ordered by start time, so the whole request can be
        traced from the first stage through every agent to the final report.
        """
        tenant = get_execution_context().actor.tenant_id
        rows = await self.db.fetch_all(
            """SELECT * FROM audit_events
               WHERE workflow_id = $1 AND tenant_id = $2
               ORDER BY created_at ASC, id ASC""",
            (workflow_id, tenant),
        )
        if not rows:
            return None

        events = [_row_to_dict(row) for row in rows]

        # Fetch workflow objective for context.
        objective = ""
        wf_row = await self.db.fetch_one(
            "SELECT output_data FROM workflow_runs WHERE id = $1 AND tenant_id = $2",
            (workflow_id, tenant),
        )
        if wf_row and wf_row.get("output_data"):
            try:
                out = json.loads(wf_row["output_data"]) if isinstance(wf_row["output_data"], str) else wf_row["output_data"]
                objective = (out or {}).get("objective", "") or ""
            except (json.JSONDecodeError, TypeError):
                objective = ""

        stages: dict[str, dict[str, Any]] = {}
        session_id = None
        trace_ids: set[str] = set()
        # Map agent_id -> pipeline node stage so remote agent-internal events
        # (llm/tool calls) can be attached to the stage that ran that agent.
        agent_to_stage: dict[str, str] = {}
        for event in events:
            meta = event.get("metadata") or {}
            node_id = meta.get("node_id")
            if event["event_type"] == "pipeline.flow.stage" and not node_id:
                # Compatibility for flows recorded before orchestration stages
                # received an explicit node identifier.
                node_id = f"flow:{meta.get('agent_id') or event.get('resource_id') or 'orchestrator'}"
            trace_ids.add(event.get("trace_id"))
            if event.get("session_id"):
                session_id = event["session_id"]
            is_remote_internal = event["event_type"].startswith("remote.")
            if is_remote_internal:
                agent_id = event.get("resource_id") or ""
                target = agent_to_stage.get(agent_id)
                if target and target in stages:
                    stages[target]["events"].append(event)
                    stages[target]["event_types"].append(event["event_type"])
                continue
            if not node_id:
                continue
            stage = stages.setdefault(
                node_id,
                {
                    "node_id": node_id,
                    "agent_id": meta.get("agent_id") or event.get("resource_id"),
                    "status": None,
                    "start_time": None,
                    "end_time": None,
                    "duration_ms": None,
                    "artifact_version_id": None,
                    "task_id": None,
                    "event_types": [],
                    "events": [],
                },
            )
            stage["events"].append(event)
            stage["event_types"].append(event["event_type"])
            agent_id = meta.get("agent_id") or event.get("resource_id")
            if agent_id:
                stage["agent_id"] = stage["agent_id"] or agent_id
                agent_to_stage.setdefault(agent_id, node_id)
            if event["event_type"] == "pipeline.node.started" and not stage["start_time"]:
                stage["start_time"] = event["created_at"]
            if event["event_type"] == "pipeline.node.completed":
                stage["end_time"] = event["created_at"]
                stage["status"] = event.get("decision") or "completed"
                stage["artifact_version_id"] = meta.get("artifact_version_id")
                stage["task_id"] = meta.get("task_id")
            if event["event_type"] == "pipeline.flow.stage":
                if not stage["start_time"]:
                    stage["start_time"] = event["created_at"]
                stage["status"] = event.get("decision") or stage["status"] or "running"
                if (event.get("decision") or "") in {"completed", "failed", "cancelled"}:
                    stage["end_time"] = event["created_at"]
            if event["event_type"] == "human_task.created":
                stage["task_id"] = event.get("resource_id")

        stages_list = sorted(stages.values(), key=lambda s: s["start_time"] or "")
        for stage in stages_list:
            stage["duration_ms"] = _duration_ms(stage["start_time"], stage["end_time"])
            if stage["artifact_version_id"] is None:
                # pull artifact_version_id from a completed event in the stage
                for ev in stage["events"]:
                    if ev.get("metadata", {}).get("artifact_version_id"):
                        stage["artifact_version_id"] = ev["metadata"]["artifact_version_id"]
                        break

        first = next((e for e in events if e.get("event_type") == "pipeline.node.started"), events[0])
        last = events[-1]
        return {
            "workflow_id": workflow_id,
            "session_id": session_id,
            "objective": objective,
            "flow_trace_id": (sorted(trace_ids)[0] if len(trace_ids) == 1 else None),
            "start_time": first.get("created_at"),
            "end_time": last.get("created_at"),
            "duration_ms": _duration_ms(first.get("created_at"), last.get("created_at")),
            "status": last.get("decision") or "completed",
            "stage_count": len(stages_list),
            "trace_ids": sorted(trace_ids),
            "stages": stages_list,
        }

    async def get_session_traces(self, session_id: str) -> list[dict[str, Any]]:
        """Return the full trace trees for every trace linked to a session."""
        tenant = get_execution_context().actor.tenant_id
        rows = await self.db.fetch_all(
            """SELECT DISTINCT trace_id FROM audit_events
               WHERE session_id = $1 AND tenant_id = $2 AND trace_id IS NOT NULL""",
            (session_id, tenant),
        )
        traces = []
        for row in rows:
            trace = await self.get_trace(row["trace_id"])
            if trace is not None:
                traces.append(trace)
        traces.sort(key=lambda t: t["start_time"] or "", reverse=True)
        return traces
