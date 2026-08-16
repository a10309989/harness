"""Remote A2A agent adapter."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC

import httpx

from harness.models.a2a import (
    A2AInvokeParams,
    A2AInvokeResult,
    A2AJsonRpcRequest,
    AgentCard,
)
from harness.models.agent import AgentConfig, AgentState
from harness.models.artifact import AgentOutcome, ArtifactDisplayRef, ArtifactType
from harness.observability.context import get_execution_context


class A2ARemoteAgentAdapter:
    """Expose a remote A2A JSON-RPC agent through the local AgentRegistry."""

    def __init__(
        self,
        *,
        card: AgentCard,
        endpoint: str,
        token: str = "",
        timeout_seconds: float = 120,
        config: AgentConfig | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        artifact_service=None,
    ) -> None:
        self.card = card
        endpoint = endpoint.rstrip("/")
        # Remote service registration stores the service base URL so the
        # discovery endpoint can be derived from it. JSON-RPC invocations,
        # however, are served at /a2a. Accept both forms at the adapter edge.
        self.endpoint = endpoint if endpoint.endswith("/a2a") else f"{endpoint}/a2a"
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.config = config
        self.artifact_service = artifact_service
        self.agent_id = card.agent_id
        self.agent_name = card.name
        self.state = AgentState.IDLE
        # Heartbeat / Agent Hub fields (populated by the heartbeat poller).
        self.last_heartbeat: str | None = None
        self.heartbeat_status: str = "unknown"  # online / offline / unknown
        self.reported_capabilities: list[dict] = []
        self.reported_skills: list[str] = []
        self.reported_runtime: str = ""
        self.reported_mcp: list[str] = []
        self.reported_tools: list[str] = []
        self.reported_prompts: list[str] = []
        self.reported_knowledge_bases: list[str] = []

    async def discover(self) -> AgentCard:
        response = await self._call("agent.discover", {"agent_id": self.agent_id})
        return AgentCard.model_validate(response)

    def update_heartbeat(
        self,
        *,
        status: str = "online",
        capabilities: list[dict] | None = None,
        skills: list[str] | None = None,
        runtime: str = "",
        mcp_servers: list[str] | None = None,
        tools: list[str] | None = None,
        prompts: list[str] | None = None,
        knowledge_bases: list[str] | None = None,
    ) -> None:
        """Update the Agent Hub heartbeat snapshot for this remote agent."""
        from datetime import datetime

        self.last_heartbeat = datetime.now(UTC).isoformat()
        self.heartbeat_status = status
        if capabilities is not None:
            self.reported_capabilities = capabilities
        if skills is not None:
            self.reported_skills = skills
        if runtime:
            self.reported_runtime = runtime
        if mcp_servers is not None:
            self.reported_mcp = mcp_servers
        if tools is not None:
            self.reported_tools = tools
        if prompts is not None:
            self.reported_prompts = prompts
        if knowledge_bases is not None:
            self.reported_knowledge_bases = knowledge_bases

    async def mark_offline(self) -> None:
        """Mark this remote agent offline (heartbeat missed)."""
        self.heartbeat_status = "offline"

    async def process(self, message: str, session_id: str) -> str:
        outcome = await self.process_structured(message, session_id)
        return outcome.message

    async def process_structured(self, message: str, session_id: str) -> AgentOutcome:
        self.state = AgentState.PROCESSING
        try:
            invocation_message = await self._with_source_artifact_content(message)
            # Poll the remote service's live execution_events while the agent runs,
            # so progress (LLM calls, tool calls) is surfaced in near real-time.
            poller = asyncio.create_task(
                self._poll_remote_events(session_id, run_key=session_id or self.agent_id)
            )
            response = await self._call(
                "agent.invoke",
                A2AInvokeParams(
                    agent_id=self.agent_id,
                    message=invocation_message,
                    session_id=session_id,
                    trace_id=get_execution_context().trace_id,
                    parent_workflow_id=get_execution_context().workflow_id,
                ).model_dump(mode="json"),
                timeout_seconds=self._invocation_timeout(),
            )
            poller.cancel()
            try:
                await poller
            except asyncio.CancelledError:
                pass
            result = A2AInvokeResult.model_validate(response)
            self.state = AgentState.IDLE
            if result.status == "suspended":
                # The remote service holds the human task. Surface the durable
                # suspension so the caller's workflow/plan checkpoint can wait
                # for the cascade and re-drive the node on resume.
                from harness.models.workflow import ExecutionSuspended, SuspensionInfo

                meta = result.metadata or {}
                raise ExecutionSuspended(
                    SuspensionInfo(
                        workflow_id=meta.get("workflow_id") or "",
                        step_id=meta.get("step_id") or "",
                        checkpoint_id=meta.get("checkpoint_id") or "",
                        human_task_id=meta.get("human_task_id") or "",
                        policy_decision_id=meta.get("policy_decision_id") or "",
                        required_approvals=int(meta.get("required_approvals", 1) or 1),
                    )
                )
            # Persist the remote agent's produced artifact contents into the
            # local ArtifactService so the local pipeline / HITL can read them
            # (the remote service stores them in its own DB, which local
            # read_version cannot reach).
            artifacts, artifact_map = await self._flush_remote_artifacts(result.artifacts, session_id)
            remote_cards = [
                ArtifactDisplayRef.model_validate(card)
                for card in (result.artifact_cards or [])
            ]
            # Fetch and persist the remote agent's per-step screenshots (UI test
            # evidence) into the local ArtifactService as image/png artifacts.
            screenshot_refs = await self._fetch_screenshots(result.metadata, session_id)
            # Persist the remote agent's internal events (LLM calls, tool calls)
            # into the local audit log so traces show which agent called which
            # LLM / tool.
            await self._flush_internal_traces(result.metadata, session_id)
            return AgentOutcome(
                message=result.message,
                status=result.status,
                artifacts=artifacts + screenshot_refs,
                artifact_cards=[
                    ArtifactDisplayRef(
                        **{
                            **artifact_map[card.version_id].model_dump(),
                            "artifact_type": card.artifact_type,
                            "name": card.name,
                            "actions": card.actions,
                            "metadata": card.metadata,
                        },
                    )
                    for card in remote_cards
                    if artifact_map.get(card.version_id)
                ],
                metadata={
                    **{k: v for k, v in result.metadata.items() if k != "agent_internal_traces"},
                    "remote_agent_id": self.agent_id,
                    "protocol": "a2a-jsonrpc",
                },
            )
        except Exception as exc:
            from harness.models.workflow import ExecutionSuspended

            if isinstance(exc, ExecutionSuspended):
                self.state = AgentState.WAITING
            else:
                self.state = AgentState.ERROR
            raise

    async def _with_source_artifact_content(self, message: str) -> str:
        """Inline a referenced local Artifact for a remote agent invocation.

        Remote A2A services deliberately do not receive local database access.
        Artifact-driven actions therefore transfer the immutable source content
        explicitly, with a bounded payload, while retaining the version ID for
        traceability.
        """
        if self.artifact_service is None or "Results from prerequisite steps:" in message:
            return message
        match = re.search(
            r"(?:artifact_version_id|version_id)\s*[:=]\s*([A-Za-z0-9_-]{12,})",
            message,
        )
        if match is None:
            return message
        version_id = match.group(1)
        try:
            _, raw_content = await self.artifact_service.read_version(version_id)
        except KeyError:
            return message
        content = raw_content.decode("utf-8", errors="replace")
        max_chars = 240_000
        if len(content) > max_chars:
            content = f"{content[:max_chars]}\n\n[Artifact content truncated at {max_chars} characters.]"
        return (
            f"{message}\n\nResults from prerequisite steps:\n"
            f"source_artifact_version_id={version_id}\n{content}"
        )

    async def _poll_remote_events(self, session_id: str, run_key: str) -> None:
        """Poll the remote service's live execution events and write them locally."""
        import httpx

        after = 0
        base = self.endpoint.removesuffix("/a2a").rstrip("/")
        url = f"{base}/trace-events"
        while True:
            try:
                async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                    r = await client.get(url, params={"run_key": run_key, "after": after})
                    if r.status_code == 200:
                        data = r.json()
                        for ev in data.get("events", []):
                            after = max(after, int(ev.get("seq", after)))
                            await self._record_remote_event(ev.get("event_type", ""), ev.get("payload"), session_id)
            except Exception:
                pass
            await asyncio.sleep(1)

    async def _fetch_screenshots(self, metadata: dict, session_id: str) -> list:
        """Fetch the remote agent's per-step screenshots and persist them locally."""
        screenshots = metadata.get("screenshots") or []
        if not screenshots or self.artifact_service is None:
            return []
        import httpx

        from harness.models.artifact import ArtifactDraft, ArtifactType

        refs = []
        base = self.endpoint.removesuffix("/a2a").rstrip("/")
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            for shot in screenshots[:20]:
                name = (shot or {}).get("name", "")
                if not name:
                    continue
                try:
                    r = await client.get(f"{base}/screenshots", params={"name": name})
                    if r.status_code != 200 or not r.content:
                        continue
                    ref = await self.artifact_service.create(
                        ArtifactDraft(
                            artifact_type=ArtifactType.GENERIC_DOCUMENT,
                            name=f"截图 {name}",
                            content=r.content,
                            media_type="image/png",
                            metadata={
                                "agent_id": self.agent_id,
                                "session_id": session_id,
                                "kind": "screenshot",
                                "screenshot_name": name,
                            },
                        )
                    )
                    refs.append(ref)
                except Exception:
                    continue
        return refs

    async def _record_remote_event(self, event_type: str, payload: str | None, session_id: str) -> None:
        if not event_type:
            return
        from harness.observability.audit import record_audit
        from harness.observability.context import child_span, get_execution_context

        try:
            import json as _json

            meta = _json.loads(payload) if payload else {}
            meta = {k: v for k, v in meta.items() if k not in {"event_type", "ts"}}
            meta["remote_agent_id"] = self.agent_id
        except Exception:
            meta = {}
        current = get_execution_context()
        with child_span(session_id=session_id or None, workflow_id=current.workflow_id or None):
            await record_audit(
                f"remote.{event_type}",
                resource_type="agent",
                resource_id=self.agent_id,
                metadata=meta,
            )

    async def _flush_internal_traces(self, metadata: dict, session_id: str) -> None:
        """Record the remote agent's internal LLM / tool events into the local audit log."""
        events = metadata.get("agent_internal_traces") or []
        if not events:
            return
        from harness.observability.audit import record_audit
        from harness.observability.context import child_span, get_execution_context

        current = get_execution_context()
        for event in events:
            event_type = event.get("event_type")
            if not event_type:
                continue
            etype = f"remote.{event_type}"
            ev_meta = {k: v for k, v in event.items() if k not in {"event_type", "ts"}}
            ev_meta["remote_agent_id"] = self.agent_id
            with child_span(
                session_id=session_id or None,
                workflow_id=current.workflow_id or None,
            ):
                await record_audit(
                    etype,
                    resource_type="agent",
                    resource_id=self.agent_id,
                    metadata=ev_meta,
                )

    async def _flush_remote_artifacts(
        self,
        remote_artifacts: list[dict],
        session_id: str,
    ) -> tuple[list, dict[str, object]]:
        """Write remote artifact contents into the local ArtifactService."""
        if not remote_artifacts or self.artifact_service is None:
            return [], {}
        from harness.models.artifact import ArtifactDraft, ArtifactType

        refs = []
        remote_to_local = {}
        for art in remote_artifacts:
            content = art.get("content")
            media_type = art.get("media_type") or "text/plain"
            if content is None:
                content = await self._download_remote_artifact(
                    art.get("version_id"),
                    media_type,
                )
            if content is None:
                continue
            try:
                artifact_type = ArtifactType(art.get("artifact_type") or "generic_document")
            except ValueError:
                artifact_type = ArtifactType.GENERIC_DOCUMENT
            ref = await self.artifact_service.create(
                ArtifactDraft(
                    artifact_type=artifact_type,
                    name=art.get("name") or f"远程 {self.agent_id} 产物",
                    content=content,
                    media_type=media_type,
                    metadata={
                        "agent_id": self.agent_id,
                        "session_id": session_id,
                        "remote_artifact_id": art.get("artifact_id"),
                        "remote_version_id": art.get("version_id"),
                        **(art.get("metadata") or {}),
                    },
                )
            )
            refs.append(ref)
            if art.get("version_id"):
                remote_to_local[art["version_id"]] = ref
        return refs, remote_to_local

    async def _download_remote_artifact(self, version_id: str | None, media_type: str) -> str | bytes | None:
        """Fetch a non-inlined remote artifact without inflating A2A RPC bodies."""
        if not version_id:
            return None
        base = self.endpoint.removesuffix("/a2a").rstrip("/")
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                response = await client.get(
                    f"{base}/artifacts/{version_id}/content",
                    headers=headers,
                )
                response.raise_for_status()
        except (httpx.HTTPError, ValueError):
            return None
        if media_type.startswith("text/") or media_type in {"application/json", "application/xml"}:
            return response.text
        return response.content

    def _invocation_timeout(self) -> float:
        """Keep A2A transport aligned with the remote agent's work budget."""
        capabilities = {capability.name for capability in self.card.capabilities}
        if "schedule_execution" in capabilities or self.agent_id == "scheduler_executor":
            # Runner Manager accepts executions up to 300 seconds. Reserve time
            # for evidence persistence and artifact transfer after the run.
            return max(self.timeout_seconds, 360)
        return self.timeout_seconds

    async def _call(
        self,
        method: str,
        params: dict,
        *,
        timeout_seconds: float | None = None,
    ) -> dict:
        request = A2AJsonRpcRequest(method=method, params=params)
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        async with httpx.AsyncClient(
            timeout=timeout_seconds or self.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.post(
                self.endpoint,
                headers=headers,
                json=request.model_dump(mode="json"),
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("error"):
            error = payload["error"]
            raise RuntimeError(error.get("message", "Remote A2A call failed"))
        result = payload.get("result") or {}
        if not isinstance(result, dict):
            raise RuntimeError("Remote A2A result must be an object")
        return result
