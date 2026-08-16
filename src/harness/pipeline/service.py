"""Multi-stage test pipeline: DAG planning, per-stage human approval, artifact passthrough.

A pipeline turns a single objective (e.g. "generate a test strategy from the
knowledge base and run the full lifecycle") into a DAG of agent stages. The DAG
is shown to the user for confirmation, then executed stage-by-stage. Each
stage's produced artifact is gated by a human approval task before the next
stage runs; the approved artifact version is passed downstream automatically.

The pipeline is tracked as a ``workflow_runs`` row whose ``output_data`` holds
the plan and per-node bookkeeping. Stage approval reuses ``HumanTaskService``
(which resumes the workflow on approval) and the durable ``SUSPENDED`` state.
"""

from __future__ import annotations

import json
import re
from typing import Any

from harness.artifacts.service import ArtifactService
from harness.core.registry import AgentRegistry
from harness.db.protocols import DatabaseProtocol
from harness.models.artifact import ArtifactDraft, ArtifactVersionRef
from harness.models.plan import ExecutionPlan, PlanNode, PlanNodeType
from harness.observability.audit import AuditService
from harness.observability.context import child_span, get_execution_context, new_id
from harness.observability.redaction import canonical_json, content_digest
from harness.workflow.human_tasks import HumanTaskService
from harness.workflow.service import WorkflowService

_PIPELINE_KIND = "pipeline"


class PipelineService:
    """Orchestrates the DAG planning, confirmation, execution and approval flow."""

    def __init__(
        self,
        db: DatabaseProtocol,
        workflow_service: WorkflowService,
        human_task_service: HumanTaskService,
        artifact_service: ArtifactService,
        agent_registry: AgentRegistry,
        audit_service: AuditService,
        agent_events=None,
    ) -> None:
        self.db = db
        self.workflow_service = workflow_service
        self.human_task_service = human_task_service
        self.artifact_service = artifact_service
        self.agent_registry = agent_registry
        self.audit_service = audit_service
        self.agent_events = agent_events

    # ─── Planning ───────────────────────────────────────────────

    async def plan_pipeline(
        self,
        *,
        session_id: str,
        objective: str,
        answers: dict[str, str] | None = None,
    ) -> dict:
        """Generate a DAG, persist it and return it for user confirmation.

        ``answers`` carries the user's clarifying answers (Q&A paired with the
        objective) so the planner plans with the user's constraints in context.
        """
        workflow_id = await self.workflow_service.create_run(
            session_id=session_id,
            input_data={"objective": objective, "kind": _PIPELINE_KIND},
        )
        # A single flow (workflow) shares ONE trace_id across every stage so the
        # whole pipeline run is one trace, not a separate trace per node/request.
        flow_trace_id = new_id()
        with child_span(session_id=session_id, workflow_id=workflow_id, trace_id=flow_trace_id):
            await self._record_flow_event(
                session_id, workflow_id, "planner", "started", "规划 Agent 正在生成测试生命周期 DAG",
            )
            plan = await self._create_plan(objective, answers)
            await self._record_flow_event(
                session_id, workflow_id, "planner", "completed", "规划 Agent 已生成测试生命周期 DAG",
                {"node_count": len(plan.nodes)},
            )
        state = {
            "kind": _PIPELINE_KIND,
            "objective": objective,
            "answers": answers or {},
            "session_id": session_id,
            "flow_trace_id": flow_trace_id,
            "plan": plan.model_dump(mode="json"),
            "node_outputs": {},
            "node_approved": [],
            "current_node": None,
            "status": "planning",
        }
        await self._save_state(workflow_id, state)
        with child_span(session_id=session_id, workflow_id=workflow_id, trace_id=flow_trace_id):
            await self.audit_service.record(
            "pipeline.planned",
            resource_type="pipeline",
            resource_id=workflow_id,
                metadata={"node_count": len(plan.nodes), "objective": objective, "flow_trace_id": flow_trace_id},
            )
        return {"workflow_id": workflow_id, "trace_id": flow_trace_id, "plan": state["plan"], "status": "planning"}

    async def get_pipeline(self, workflow_id: str) -> dict:
        state = await self._load_state(workflow_id)
        return {
            "workflow_id": workflow_id,
            "plan": state["plan"],
            "status": state["status"],
            "current_node": state.get("current_node"),
            "node_outputs": state["node_outputs"],
            "node_approved": state["node_approved"],
            "trace_id": state.get("flow_trace_id"),
        }

    # ─── Execution / confirmation ───────────────────────────────

    async def confirm_pipeline(self, workflow_id: str) -> dict:
        """Begin executing the confirmed DAG; returns the first pending approval."""
        state = await self._load_state(workflow_id)
        plan = ExecutionPlan.model_validate(state["plan"])
        next_node = next(
            (
                node
                for node in plan.nodes
                if node.id not in set(state["node_approved"])
                and set(node.depends_on).issubset(set(state["node_approved"]))
            ),
            None,
        )
        if next_node is not None and next_node.node_type == PlanNodeType.HUMAN:
            state["current_node"] = next_node.id
            state["status"] = "waiting_requirements_confirmation"
            await self._save_state(workflow_id, state)
            await self._set_workflow_state(workflow_id, "suspended")
            await self._record_flow_event(
                state.get("session_id", ""), workflow_id, "requirements_analyst", "waiting",
                "需求分析 Agent 等待需求确认后生成测试策略",
                trace_id=state.get("flow_trace_id"),
            )
            return {
                "workflow_id": workflow_id,
                "status": state["status"],
                "current_node": next_node.id,
                "plan": state["plan"],
            }
        state["status"] = "running"
        await self._save_state(workflow_id, state)
        await self._set_workflow_state(workflow_id, "running")
        await self._record_flow_event(
            state.get("session_id", ""), workflow_id, "requirements_analyst", "completed",
            "需求分析 Agent 已确认需求并产出测试策略",
            {"pipeline_confirmation": True}, state.get("flow_trace_id"),
        )
        return await self._run_next(workflow_id)

    async def _record_flow_event(self, session_id: str, workflow_id: str, agent_id: str, status: str, message: str, metadata: dict | None = None, trace_id: str | None = None) -> None:
        if self.agent_events is None:
            return
        with child_span(session_id=session_id or None, workflow_id=workflow_id, trace_id=trace_id):
            await self.agent_events.record(
                "pipeline.flow.stage", session_id=session_id, agent_id=agent_id,
                step=agent_id, status=status, message=message,
                metadata={
                    "workflow_id": workflow_id,
                    "node_id": f"flow:{agent_id}",
                    "stage_kind": "orchestration",
                    **(metadata or {}),
                },
            )

    async def resume_pipeline(self, workflow_id: str) -> dict:
        """Continue after an approval: mark the current node approved, run next."""
        state = await self._load_state(workflow_id)
        current = state.get("current_node")
        plan = ExecutionPlan.model_validate(state["plan"])
        current_node = next((node for node in plan.nodes if node.id == current), None)
        if current_node is not None and current_node.node_type == PlanNodeType.HUMAN:
            raise ValueError("Requirements confirmation must be completed before resuming this pipeline")
        if current and current not in state["node_approved"]:
            state["node_approved"].append(current)
        state["current_node"] = None
        await self._save_state(workflow_id, state)
        return await self._run_next(workflow_id)

    async def retry_pipeline(self, workflow_id: str) -> dict:
        """Retry an interrupted in-flight agent node without skipping its output.

        A process restart can interrupt an A2A call after the runner accepted a
        job but before the pipeline persisted the artifact.  The node has no
        output in that state, so it is safe to clear only the transient marker
        and let normal dependency selection invoke the same node again.
        """
        state = await self._load_state(workflow_id)
        current = state.get("current_node")
        if not current or current in state.get("node_outputs", {}):
            raise ValueError("Pipeline has no interrupted node to retry")
        state["current_node"] = None
        state["status"] = "running"
        await self._save_state(workflow_id, state)
        await self._set_workflow_state(workflow_id, "running")
        return await self._run_next(workflow_id)

    async def complete_requirements_confirmation(
        self,
        workflow_id: str,
        strategy_artifact_version_id: str,
    ) -> dict:
        """Use the confirmed strategy as the output of the initial human DAG node."""
        state = await self._load_state(workflow_id)
        current = state.get("current_node")
        plan = ExecutionPlan.model_validate(state["plan"])
        node = next((item for item in plan.nodes if item.id == current), None)
        if node is None or node.node_type != PlanNodeType.HUMAN:
            raise ValueError("Pipeline is not waiting for requirements confirmation")
        if current not in state["node_approved"]:
            state["node_approved"].append(current)
        state["node_outputs"][current] = strategy_artifact_version_id
        state["current_node"] = None
        state["status"] = "running"
        await self._save_state(workflow_id, state)
        await self._set_workflow_state(workflow_id, "running")
        return await self._run_next(workflow_id)

    async def revise_test_case_draft(self, workflow_id: str, content: str) -> dict:
        state, node = await self._test_case_draft_state(workflow_id)
        current_version_id = state["node_outputs"][node.id]
        version, _ = await self.artifact_service.read_version(current_version_id)
        artifact = await self.artifact_service.create_version(
            version["artifact_id"],
            content,
            media_type=version["media_type"],
            metadata={**(version.get("metadata") or {}), "draft_revision": "user_edit"},
        )
        await self._replace_test_case_output(workflow_id, state, node, artifact.version_id)
        return artifact.model_dump(mode="json")

    async def regenerate_test_case_draft(self, workflow_id: str, feedback: str) -> dict:
        state, node = await self._test_case_draft_state(workflow_id)
        revised_node = node.model_copy(
            update={"input_data": {**node.input_data, "user_feedback": feedback}}
        )
        plan = ExecutionPlan.model_validate(state["plan"])
        artifact = await self._run_node(workflow_id, plan, revised_node, state["node_outputs"], state)
        await self._replace_test_case_output(workflow_id, state, node, artifact.version_id)
        return artifact.model_dump(mode="json")

    async def _test_case_draft_state(self, workflow_id: str) -> tuple[dict, PlanNode]:
        state = await self._load_state(workflow_id)
        if state.get("status") != "waiting_approval":
            raise ValueError("Pipeline is not waiting for test-case approval")
        plan = ExecutionPlan.model_validate(state["plan"])
        current_node_id = state.get("current_node")
        node = next(
            (
                item
                for item in plan.nodes
                if item.id == current_node_id and item.agent_id == "test_case_generator"
            ),
            None,
        )
        if node is None or node.id not in state.get("node_outputs", {}):
            raise ValueError("Test-case draft is unavailable")
        return state, node

    async def _replace_test_case_output(
        self,
        workflow_id: str,
        state: dict,
        node: PlanNode,
        version_id: str,
    ) -> None:
        state["node_outputs"][node.id] = version_id
        await self._save_state(workflow_id, state)
        rows = await self.db.fetch_all(
            "SELECT id, metadata FROM human_tasks WHERE workflow_id = $1 AND state IN ($2, $3)",
            (workflow_id, "pending", "claimed"),
        )
        for row in rows:
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
            if metadata.get("node_id") != node.id:
                continue
            metadata["artifact_version_id"] = version_id
            await self.db.execute(
                "UPDATE human_tasks SET metadata = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
                (json.dumps(metadata, ensure_ascii=False), row["id"]),
            )
        await self.db.commit()

    async def _run_next(self, workflow_id: str) -> dict:
        """Run the next ready stage (one node per approval) or complete."""
        state = await self._load_state(workflow_id)
        plan = ExecutionPlan.model_validate(state["plan"])
        approved = set(state["node_approved"])
        outputs = state["node_outputs"]

        next_node = next(
            (
                node
                for node in plan.nodes
                if node.id not in approved
                and set(node.depends_on).issubset(approved)
            ),
            None,
        )
        if next_node is None:
            state["status"] = "completed"
            await self._save_state(workflow_id, state)
            await self._set_workflow_state(workflow_id, "completed")
            return {
                "workflow_id": workflow_id,
                "status": "completed",
                "plan": state["plan"],
                "node_outputs": state["node_outputs"],
            }

        # Mark the node as RUNNING *before* executing so the frontend DAG can
        # show an in-progress state while the agent works, not only after it
        # completes (which the old code showed as straight pending->completed).
        state["current_node"] = next_node.id
        state["status"] = "running"
        await self._save_state(workflow_id, state)
        await self._set_workflow_state(workflow_id, "running")

        from harness.observability.context import child_span

        flow_trace_id = state.get("flow_trace_id")
        with child_span(
            session_id=state.get("session_id") or None,
            workflow_id=workflow_id,
            trace_id=flow_trace_id or None,
        ):
            artifact = await self._run_node(workflow_id, plan, next_node, outputs, state)
            outputs[next_node.id] = artifact.version_id
            task_id = await self._gate_artifact(workflow_id, next_node, artifact, plan.objective, state)
        state["current_node"] = next_node.id
        state["status"] = "waiting_approval"
        await self._save_state(workflow_id, state)
        await self._set_workflow_state(workflow_id, "suspended")
        return {
            "workflow_id": workflow_id,
            "status": "waiting_approval",
            "current_node": next_node.id,
            "pending_tasks": [
                {
                    "task_id": task_id,
                    "node_id": next_node.id,
                    "artifact_version_id": artifact.version_id,
                }
            ],
            "plan": state["plan"],
            "node_outputs": state["node_outputs"],
        }

    async def _run_node(
        self,
        workflow_id: str,
        plan: ExecutionPlan,
        node: PlanNode,
        outputs: dict[str, str],
        state: dict[str, Any],
    ) -> ArtifactVersionRef:
        """Invoke the node's agent with upstream artifacts as input; persist output."""
        agent = self.agent_registry.get_agent(node.agent_id or "")
        prompt = (
            f"Overall objective: {plan.objective}\n\n"
            f"Your assigned step: {node.description}\n"
        )
        if node.input_data:
            prompt += "\nStructured input:\n" + json.dumps(node.input_data, ensure_ascii=False, indent=2) + "\n"
        # Upstream artifact references + content. Artifact-driven agents
        # (test_case_generator, script_generator) extract the version_id to
        # read their real input artifact, so we must pass it explicitly.
        upstream_refs = []
        upstream_content = []
        source_version_id = node.input_data.get("source_artifact_version_id")
        if isinstance(source_version_id, str) and source_version_id:
            upstream_refs.append(f"[source] version_id={source_version_id}")
            _, content = await self.artifact_service.read_version(source_version_id)
            upstream_content.append(f"[source]\n{content.decode('utf-8', errors='replace')}")
        for dep in node.depends_on:
            version_id = outputs.get(dep)
            if version_id:
                upstream_refs.append(f"[{dep}] version_id={version_id}")
                _, content = await self.artifact_service.read_version(version_id)
                upstream_content.append(f"[{dep}]\n{content.decode('utf-8', errors='replace')}")
        if upstream_refs:
            prompt += "\nUpstream artifact references (read via version_id):\n" + "\n".join(upstream_refs) + "\n"
        if upstream_content:
            prompt += "\nResults from prerequisite steps:\n" + "\n\n".join(upstream_content) + "\n"
        session_id = state.get("session_id") or ""
        agent_name = getattr(agent, "agent_name", node.agent_id)
        await self._record_stage_event(
            session_id, node, workflow_id=workflow_id,
            step="started", status="running",
            message=f"🔄 开始执行：{agent_name} — {node.description}",
            metadata={"agent_id": node.agent_id, "node_id": node.id},
        )
        from harness.observability.context import child_span

        with child_span(session_id=session_id or None, workflow_id=workflow_id or None):
            outcome = await agent.process_structured(prompt, session_id)
        # Prefer the agent's own produced artifacts (real strategy / test cases
        # / script / report). Screenshots (image/png) are evidence, NOT the stage
        # output — pick the last non-screenshot artifact (prefer text/html report).
        real_artifacts = [
            ref
            for ref in (outcome.artifacts or [])
            if ref.media_type != "image/png"
        ]
        if real_artifacts:
            artifact = next(
                (ref for ref in reversed(real_artifacts) if ref.media_type == "text/html"),
                real_artifacts[-1],
            )
        else:
            artifact = await self.artifact_service.create(
                ArtifactDraft(
                    artifact_type=self._artifact_type_for(node),
                    name=f"{node.description} ({node.id})",
                    content=outcome.message or "",
                    media_type="text/markdown",
                    metadata={"workflow_id": workflow_id, "node_id": node.id, "agent_id": node.agent_id},
                )
            )
        # Collect screenshot artifacts (image/png) produced by the agent so the
        # stage approval task can surface them in the frontend approval card.
        screenshot_ids = [
            ref.version_id
            for ref in (outcome.artifacts or [])
            if ref.media_type == "image/png"
        ]
        execution_steps = (outcome.metadata or {}).get("execution_steps") or []
        artifact = await self._attach_screenshot_evidence(
            artifact,
            screenshot_ids,
            execution_steps,
        )
        state["last_screenshot_ids"] = screenshot_ids
        await self._record_stage_event(
            session_id, node, workflow_id=workflow_id,
            step="completed", status="completed",
            message=f"✅ 产物已生成：{agent_name} — {node.description}",
            metadata={
                "agent_id": node.agent_id,
                "node_id": node.id,
                "artifact_version_id": artifact.version_id,
                "screenshot_ids": screenshot_ids,
                "artifact_cards": [card.model_dump(mode="json") for card in (outcome.artifact_cards or [])],
            },
        )
        return artifact

    async def _attach_screenshot_evidence(
        self,
        artifact: ArtifactVersionRef,
        screenshot_ids: list[str],
        execution_steps: list[dict[str, Any]],
    ) -> ArtifactVersionRef:
        """Version execution reports with immutable local screenshot references."""
        if not screenshot_ids or artifact.media_type != "application/json":
            return artifact
        try:
            version, content = await self.artifact_service.read_version(artifact.version_id)
            report = json.loads(content.decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
            return artifact
        if not isinstance(report, dict) or not isinstance(report.get("result"), dict):
            return artifact
        if report.get("screenshot_artifact_version_ids") == screenshot_ids:
            return artifact
        report["screenshot_artifact_version_ids"] = screenshot_ids
        report["screenshot_evidence"] = [
            {
                "version_id": version_id,
                "screenshot_name": (
                    execution_steps[index].get("screenshot_name")
                    if index < len(execution_steps) and isinstance(execution_steps[index], dict)
                    else None
                ),
            }
            for index, version_id in enumerate(screenshot_ids)
        ]
        return await self.artifact_service.create_version(
            artifact.artifact_id,
            json.dumps(report, ensure_ascii=False, indent=2),
            media_type=artifact.media_type,
            metadata={
                **(version.get("metadata") or {}),
                "screenshot_artifact_version_ids": screenshot_ids,
            },
        )

    async def _record_stage_event(
        self,
        session_id: str,
        node: PlanNode,
        *,
        workflow_id: str | None = None,
        step: str,
        status: str,
        message: str,
        metadata: dict[str, Any],
    ) -> None:
        """Emit a dialog-visible stage event (started / completed) for a pipeline node."""
        if self.agent_events is None:
            return
        # Wrap in a child span carrying session_id + workflow_id so the audit
        # event's ``session_id`` / ``workflow_id`` columns are populated and the
        # whole pipeline can be traced by session or by flow (workflow_id).
        from harness.observability.context import child_span

        with child_span(session_id=session_id or None, workflow_id=workflow_id or None):
            await self.agent_events.record(
                f"pipeline.node.{step}",
                session_id=session_id,
                agent_id=node.agent_id or "",
                step=node.id,
                status=status,
                message=message,
                metadata=metadata,
            )

    async def _gate_artifact(
        self,
        workflow_id: str,
        node: PlanNode,
        artifact: ArtifactVersionRef,
        objective: str,
        state: dict[str, Any] | None = None,
    ) -> str | None:
        """Create a durable checkpoint + human approval task gating this artifact."""
        context = get_execution_context()
        step_id = new_id()
        checkpoint_id = new_id()
        now = self._now()
        # Checkpoint artifact carries the linkage so `approve` can resume the
        # pipeline with the produced artifact version.
        checkpoint_artifact = await self.artifact_service.create(
            ArtifactDraft(
                artifact_type=self._artifact_type_for(node),
                name=f"Pipeline checkpoint {node.id}",
                content=canonical_json(
                    {
                        "kind": _PIPELINE_KIND,
                        "workflow_id": workflow_id,
                        "node_id": node.id,
                        "artifact_version_id": artifact.version_id,
                        "artifact_digest": artifact.content_digest,
                    }
                ),
                media_type="application/json",
                metadata={"workflow_id": workflow_id, "node_id": node.id},
            )
        )
        sequence_number = await self._next_sequence(workflow_id)
        async with self.db.transaction() as connection:
            await connection.execute(
                """INSERT INTO workflow_steps
                       (id, workflow_id, sequence_number, step_type, agent_id,
                        resource_id, state, input_digest, created_at, started_at)
                       VALUES ($1, $2, $3, 'pipeline', $4, $5, 'waiting', $6, $7, $7)""",
                (
                    step_id,
                    workflow_id,
                    sequence_number,
                    node.agent_id,
                    node.id,
                    content_digest(node.input_data),
                    now,
                ),
            )
            await connection.execute(
                """INSERT INTO workflow_checkpoints
                       (id, workflow_id, step_id, artifact_version_id,
                        checkpoint_type, created_at)
                       VALUES ($1, $2, $3, $4, 'pipeline', $5)""",
                (
                    checkpoint_id,
                    workflow_id,
                    step_id,
                    checkpoint_artifact.version_id,
                    now,
                ),
            )
        task_id = await self.human_task_service.create_task(
            workflow_id=workflow_id,
            title=f"审批产物: {node.description}",
            description=f"Pipeline stage '{node.id}' produced an artifact. Approve to continue.",
            checkpoint_id=checkpoint_id,
            metadata={
                "kind": _PIPELINE_KIND,
                "node_id": node.id,
                "artifact_version_id": artifact.version_id,
                "screenshot_ids": (state or {}).get("last_screenshot_ids") or [],
            },
        )
        return task_id

    # ─── Helpers ────────────────────────────────────────────────

    async def _create_plan(
        self,
        objective: str,
        answers: dict[str, str] | None = None,
    ) -> ExecutionPlan:
        # Fold the user's clarifying answers into the objective so the planner
        # plans with the user's constraints (doc, report format, scope, ...).
        if answers:
            qa = "\n".join(f"- {k}: {v}" for k, v in answers.items() if v)
            if qa:
                objective = f"{objective}\n\nUser's clarifications:\n{qa}"

        try:
            planner = self.agent_registry.get_agent("planner")
        except KeyError as exc:
            raise RuntimeError("Planner Agent is unavailable; DAG generation cannot continue") from exc
        outcome = await planner.process_structured(
            "[force-pipeline-planning]\n" + objective,
            "pipeline-planning",
        )
        payload = (outcome.metadata or {}).get("plan")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = None
        if not isinstance(payload, dict):
            try:
                payload = json.loads(outcome.message)
            except (TypeError, json.JSONDecodeError):
                payload = None
        if not isinstance(payload, dict):
            raise RuntimeError("Planner Agent did not return a structured DAG")
        from harness.core.planner import PlanCompiler

        return PlanCompiler(self.agent_registry).compile(ExecutionPlan.model_validate(payload))

    def _is_test_lifecycle(self, objective: str) -> bool:
        text = objective.lower()
        lifecycle_words = [
            "需求", "用例", "脚本", "执行", "报告", "测试",
            "requirement", "test case", "script", "report", "execute",
        ]
        return sum(1 for word in lifecycle_words if word in text) >= 2

    def _fixed_test_lifecycle_plan(self, objective: str) -> ExecutionPlan | None:
        """A deterministic test-lifecycle DAG when all lifecycle agents exist."""
        if not self._is_test_lifecycle(objective):
            return None
        required = {
            "requirements_analyst",
            "test_case_generator",
            "script_generator",
            "scheduler_executor",
            "log_analyst",
        }
        available = set(self.agent_registry.list_agents())
        if not required.issubset(available):
            return None
        from harness.core.planner import PlanCompiler

        source_match = re.search(r"version_id=([A-Za-z0-9_-]+)", objective)
        source_version_id = source_match.group(1) if source_match else None
        continue_from_strategy = source_version_id is not None and (
            "已确认测试策略" in objective or "approved test strategy" in objective.lower()
        )
        nodes = [] if continue_from_strategy else [
            PlanNode(
                id="requirements_confirmation", node_type=PlanNodeType.HUMAN,
                description="需求分析、思维导图确认与测试策略生成", depends_on=[],
            ),
        ]
        nodes.extend([
            PlanNode(
                id="test_cases", node_type=PlanNodeType.AGENT,
                agent_id="test_case_generator",
                description="基于测试策略生成测试用例",
                depends_on=[] if continue_from_strategy else ["requirements_confirmation"],
                input_data={"source_artifact_version_id": source_version_id} if continue_from_strategy else {},
            ),
            PlanNode(
                id="scripts", node_type=PlanNodeType.AGENT,
                agent_id="script_generator",
                description="基于测试用例生成自动化脚本", depends_on=["test_cases"],
            ),
            PlanNode(
                id="execute", node_type=PlanNodeType.AGENT,
                agent_id="scheduler_executor",
                description="执行自动化测试", depends_on=["scripts"],
            ),
            PlanNode(
                id="report", node_type=PlanNodeType.AGENT,
                agent_id="log_analyst",
                description="分析执行结果，生成测试报告", depends_on=["execute"],
            ),
        ])
        return PlanCompiler(self.agent_registry).compile(
            ExecutionPlan(objective=objective, nodes=nodes)
        )

    @staticmethod
    def _artifact_type_for(node: PlanNode):
        from harness.models.artifact import ArtifactType

        mapping = {
            "requirements": ArtifactType.REQUIREMENT_ANALYSIS_PACKAGE,
            "test_case": ArtifactType.TEST_CASE_SET,
            "script": ArtifactType.TEST_SCRIPT,
        }
        agent = node.agent_id or ""
        for key, value in mapping.items():
            if key in agent:
                return value
        return ArtifactType.GENERIC_DOCUMENT

    async def _save_state(self, workflow_id: str, state: dict[str, Any]) -> None:
        await self.db.execute(
            "UPDATE workflow_runs SET output_data = $1 WHERE id = $2",
            (json.dumps(state, ensure_ascii=False), workflow_id),
        )
        await self.db.commit()

    async def _load_state(self, workflow_id: str) -> dict[str, Any]:
        row = await self.db.fetch_one(
            "SELECT output_data FROM workflow_runs WHERE id = $1",
            (workflow_id,),
        )
        if not row or not row.get("output_data"):
            raise KeyError(f"Pipeline '{workflow_id}' not found")
        state = json.loads(row["output_data"])
        if state.get("kind") != _PIPELINE_KIND:
            raise KeyError(f"Workflow '{workflow_id}' is not a pipeline")
        return state

    async def _next_sequence(self, workflow_id: str) -> int:
        row = await self.db.fetch_one(
            "SELECT COALESCE(MAX(sequence_number), 0) AS n FROM workflow_steps WHERE workflow_id = $1",
            (workflow_id,),
        )
        return int(row["n"]) + 1

    @staticmethod
    def _now():
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).replace(tzinfo=None)

    async def _set_workflow_state(self, workflow_id: str, state: str) -> None:
        await self.db.execute(
            "UPDATE workflow_runs SET state = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
            (state, workflow_id),
        )
        await self.db.commit()
