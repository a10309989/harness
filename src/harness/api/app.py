"""FastAPI application factory for the Harness framework."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from harness.a2a.heartbeat import HeartbeatPoller
from harness.api.config_store import ConfigStore
from harness.api.routes import (
    a2a,
    agents,
    artifacts,
    audit,
    evaluations,
    executions,
    human_tasks,
    identity,
    knowledge,
    mcp,
    ops,
    pipelines,
    policies,
    sessions,
    settings,
    skills,
    test_cases,
    tools,
    traces,
    workflows,
)
from harness.artifacts.service import ArtifactService
from harness.artifacts.storage import LocalCAS, MinIOStorage
from harness.core.events import EventBus
from harness.core.registry import AgentRegistry
from harness.core.session import SessionManager
from harness.db import create_database
from harness.db.protocols import DatabaseProtocol
from harness.evaluation.service import EvaluationService
from harness.knowledge.service import KnowledgeService
from harness.mcp.manager import MCPManager
from harness.observability.agent_events import AgentEventService
from harness.observability.audit import AuditService, set_default_audit_service
from harness.observability.otel import configure_otel
from harness.observability.outbox import OutboxWorker
from harness.observability.trace import TraceService
from harness.policy.engine import PolicyEngine
from harness.runtime.services import RuntimeServices
from harness.runtime.settings import RuntimeSettings
from harness.security.auth import AuthService
from harness.security.gateway import GatewayMiddleware
from harness.security.middleware import SecurityContextMiddleware
from harness.security.ratelimit import RateLimitConfig, RateLimiter
from harness.skills.registry import SkillRegistry
from harness.workflow.human_tasks import HumanTaskService
from harness.workflow.runtime import ResumeWorker
from harness.workflow.service import WorkflowService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    # Initialize Database first (required by ConfigStore and SessionManager)
    settings = app.state.settings
    db = create_database(settings.database_url)
    await db.initialize()
    app.state.database = db

    # Initialize all shared state
    app.state.event_bus = EventBus()
    app.state.auth_service = AuthService(db, mode=settings.security_mode)
    await app.state.auth_service.initialize()
    app.state.rate_limiter = RateLimiter(
        RateLimitConfig(
            enabled=settings.rate_limit_enabled,
            requests_per_minute=settings.rate_limit_requests_per_minute,
            auth_failures_per_minute=settings.rate_limit_auth_failures_per_minute,
            auth_failure_window_seconds=settings.rate_limit_auth_failure_window_seconds,
        )
    )
    app.state.audit_service = AuditService(db)
    set_default_audit_service(app.state.audit_service)
    app.state.agent_event_service = AgentEventService(app.state.audit_service)
    app.state.trace_service = TraceService(db)
    storage = LocalCAS(str(settings.artifact_storage_path))
    if settings.artifact_storage_backend == "minio":
        storage = MinIOStorage(
            settings.minio_endpoint,
            settings.minio_bucket,
            settings.minio_access_key,
            settings.minio_secret_key,
            secure=settings.minio_secure,
        )
    elif settings.artifact_storage_backend != "local":
        raise RuntimeError("HARNESS_ARTIFACT_STORAGE_BACKEND must be 'local' or 'minio'")
    app.state.artifact_service = ArtifactService(
        db,
        storage,
        app.state.audit_service,
    )
    app.state.knowledge_service = KnowledgeService(
        db,
        app.state.artifact_service,
        app.state.audit_service,
    )
    await app.state.knowledge_service.initialize_defaults()
    app.state.policy_engine = PolicyEngine(db, app.state.audit_service)
    await app.state.policy_engine.initialize()
    app.state.workflow_service = WorkflowService(
        db,
        app.state.artifact_service,
        app.state.audit_service,
    )
    app.state.evaluation_service = EvaluationService(db, app.state.audit_service)
    app.state.human_task_service = HumanTaskService(
        db,
        app.state.workflow_service,
        app.state.artifact_service,
        app.state.audit_service,
        dev_mode=(settings.security_mode == "dev"),
    )
    app.state.runtime_services = RuntimeServices(
        audit=app.state.audit_service,
        agent_events=app.state.agent_event_service,
        artifacts=app.state.artifact_service,
        knowledge=app.state.knowledge_service,
        policy=app.state.policy_engine,
        workflows=app.state.workflow_service,
        human_tasks=app.state.human_task_service,
    )
    recovered_runs = await app.state.workflow_service.recover_interrupted_runs()
    if recovered_runs:
        logger.warning(
            "Moved %s interrupted workflow(s) to recovery_required",
            recovered_runs,
        )
    app.state.redis_event_publisher = None
    if settings.redis_events_enabled:
        from harness.messaging.redis_events import RedisEventPublisher

        app.state.redis_event_publisher = await RedisEventPublisher.connect(settings)
    app.state.outbox_worker = OutboxWorker(
        db,
        app.state.audit_service,
        app.state.event_bus,
        redis_publisher=app.state.redis_event_publisher,
    )
    app.state.agent_registry = AgentRegistry()
    app.state.skill_registry = SkillRegistry()
    app.state.mcp_manager = MCPManager()
    app.state.config_store = ConfigStore(db)
    app.state.session_manager = SessionManager(db)
    from harness.pipeline.service import PipelineService

    app.state.pipeline_service = PipelineService(
        db,
        app.state.workflow_service,
        app.state.human_task_service,
        app.state.artifact_service,
        app.state.agent_registry,
        app.state.audit_service,
        app.state.agent_event_service,
    )
    app.state.temporal_approval_client = None
    if settings.temporal_enabled:
        from harness.temporal.client import TemporalApprovalClient

        app.state.temporal_approval_client = await TemporalApprovalClient.connect(
            settings,
            app.state.workflow_service,
        )

    # Load existing sessions from DB
    await app.state.session_manager.initialize()

    # Migrate YAML data to SQLite if needed (first run)
    await _migrate_yaml_data(db, app.state.config_store)

    # Wire up all agents from YAML config
    from harness.api.wire_agents import wire_all_agents
    try:
        master = await wire_all_agents(
            registry=app.state.agent_registry,
            event_bus=app.state.event_bus,
            skill_registry=app.state.skill_registry,
            config_store=app.state.config_store,
            runtime_services=app.state.runtime_services,
        )
    except Exception:
        await app.state.mcp_manager.close_all()
        await app.state.session_manager.close_all()
        set_default_audit_service(None)
        await db.close()
        raise
    await _import_configured_remote_a2a_agents(app)
    app.state.resume_worker = ResumeWorker(
        app.state.workflow_service,
        app.state.human_task_service,
        app.state.agent_registry,
        app.state.session_manager,
    )
    logger.info(f"MasterAgent '{master.agent_name}' ready — {len(app.state.agent_registry.list_agents())} agent(s) registered")

    # Stateless-master driver: advances submitted plan-runs in the background
    # (multi-replica safe via idempotent per-node progress in the control DB).
    from harness.core.execution import WorkflowDriver

    app.state.workflow_driver = WorkflowDriver(
        app.state.agent_registry, app.state.workflow_service
    )

    async def _driver_loop():
        while True:
            try:
                await app.state.workflow_driver.drain_once()
            except Exception:
                logger.exception("WorkflowDriver pass failed")
            await asyncio.sleep(0.5)

    driver_task = asyncio.create_task(_driver_loop())

    cleanup_task = asyncio.create_task(app.state.session_manager.cleanup_loop())
    outbox_task = asyncio.create_task(app.state.outbox_worker.run())
    resume_task = None
    if settings.legacy_resume_worker_enabled:
        resume_task = asyncio.create_task(app.state.resume_worker.run())

    heartbeat_task = asyncio.create_task(
        HeartbeatPoller(db, app.state.agent_registry).run()
    )



    temporal_worker_task = None
    if settings.temporal_worker_in_api:
        from harness.temporal.worker import run_worker

        temporal_worker_task = asyncio.create_task(
            run_worker(
                settings,
                resumer=app.state.resume_worker.resumer,
                audit_service=app.state.audit_service,
            )
        )

    logger.info("Harness application startup complete")
    yield

    # Shutdown
    cleanup_task.cancel()
    await app.state.outbox_worker.stop()
    await app.state.resume_worker.stop()
    outbox_task.cancel()
    heartbeat_task.cancel()
    if resume_task:
        resume_task.cancel()
        driver_task.cancel()
    if temporal_worker_task:
        temporal_worker_task.cancel()
    await asyncio.gather(
        cleanup_task,
        outbox_task,
        heartbeat_task,
        *([resume_task] if resume_task else []),
        *([temporal_worker_task] if temporal_worker_task else []),
        return_exceptions=True,
    )
    await app.state.mcp_manager.close_all()
    if app.state.redis_event_publisher is not None:
        await app.state.redis_event_publisher.close()
    await app.state.session_manager.close_all()
    set_default_audit_service(None)
    await db.close()
    logger.info("Harness application shutdown complete")


async def _migrate_yaml_data(db: DatabaseProtocol, config_store: ConfigStore) -> None:
    """Migrate YAML config data to SQLite on first run.

    Checks if llm_providers table is empty and YAML files exist.
    If so, migrates the data and renames YAML files to .yaml.bak.
    """
    from pathlib import Path

    import yaml

    # Check if migration is needed
    existing = await db.fetch_all("SELECT COUNT(*) as cnt FROM llm_providers")
    count = existing[0]["cnt"] if existing else 0
    if count > 0:
        return  # Already migrated

    config_dir = Path("config")
    migrated = False

    # Migrate llm_providers.yaml
    llm_path = config_dir / "llm_providers.yaml"
    if llm_path.exists():
        with open(llm_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for name, config in data.items():
            await config_store.save_llm_provider(name, config)
        llm_path.rename(llm_path.with_suffix(".yaml.bak"))
        logger.info(f"Migrated {len(data)} LLM provider(s) from YAML to SQLite")
        migrated = True

    # Migrate mcp_servers.yaml
    mcp_path = config_dir / "mcp_servers.yaml"
    if mcp_path.exists():
        with open(mcp_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for name, config in data.items():
            await config_store.save_mcp_server(name, config)
        mcp_path.rename(mcp_path.with_suffix(".yaml.bak"))
        logger.info(f"Migrated {len(data)} MCP server(s) from YAML to SQLite")
        migrated = True

    # Migrate agent_mcp_bindings.yaml
    bindings_path = config_dir / "agent_mcp_bindings.yaml"
    if bindings_path.exists():
        with open(bindings_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for agent_id, bindings in data.items():
            await config_store.set_agent_mcp_bindings(agent_id, bindings)
        bindings_path.rename(bindings_path.with_suffix(".yaml.bak"))
        logger.info("Migrated agent-MCP bindings from YAML to SQLite")
        migrated = True

    # Migrate agent_llm_overrides.yaml
    overrides_path = config_dir / "agent_llm_overrides.yaml"
    if overrides_path.exists():
        with open(overrides_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for agent_id, override in data.items():
            await config_store.set("agent_llm_overrides", agent_id, override)
        overrides_path.rename(overrides_path.with_suffix(".yaml.bak"))
        logger.info("Migrated agent-LLM overrides from YAML to SQLite")
        migrated = True

    if migrated:
        logger.info("YAML → SQLite migration complete")


def create_app(runtime_settings: RuntimeSettings | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Harness — Test Automation Agent Framework",
        description="AI-driven test lifecycle management",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.settings = runtime_settings or RuntimeSettings()
    configure_otel(app, app.state.settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=app.state.settings.allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SecurityContextMiddleware)
    app.add_middleware(GatewayMiddleware)

    app.include_router(sessions.router, prefix="/api/v1/sessions", tags=["Sessions"])
    app.include_router(agents.router, prefix="/api/v1/agents", tags=["Agents"])
    app.include_router(test_cases.router, prefix="/api/v1/test-cases", tags=["Test Cases"])
    app.include_router(executions.router, prefix="/api/v1/executions", tags=["Executions"])
    app.include_router(knowledge.router, prefix="/api/v1/knowledge", tags=["Knowledge"])
    app.include_router(skills.router, prefix="/api/v1/skills", tags=["Skills"])
    app.include_router(mcp.router, prefix="/api/v1/mcp", tags=["MCP"])
    app.include_router(tools.router, prefix="/api/v1/tools", tags=["Tools"])
    app.include_router(settings.router, prefix="/api/v1/settings", tags=["Settings"])
    app.include_router(identity.router, prefix="/api/v1/identity", tags=["Identity"])
    app.include_router(audit.router, prefix="/api/v1/audit", tags=["Audit"])
    app.include_router(artifacts.router, prefix="/api/v1/artifacts", tags=["Artifacts"])
    app.include_router(policies.router, prefix="/api/v1/policies", tags=["Policies"])
    app.include_router(workflows.router, prefix="/api/v1/workflows", tags=["Workflows"])
    app.include_router(pipelines.router, prefix="/api/v1/pipelines", tags=["Pipelines"])
    app.include_router(human_tasks.router, prefix="/api/v1/human-tasks", tags=["Human Tasks"])
    app.include_router(evaluations.router, prefix="/api/v1/evaluations", tags=["Evaluations"])
    app.include_router(ops.router, prefix="/api/v1/ops", tags=["Operations"])
    app.include_router(traces.router, prefix="/api/v1/traces", tags=["Traces"])
    app.include_router(a2a.router, prefix="/api/v1/a2a", tags=["A2A"])

    @app.get("/health")
    async def health_check():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/trace", response_class=HTMLResponse, include_in_schema=False)
    async def trace_viewer():
        return _TRACE_VIEWER_HTML

    return app


_TRACE_VIEWER_HTML = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>全链路 Trace 查看</title>
<style>
  :root { --bg:#0f1420; --panel:#171e2e; --line:#2a3550; --text:#dbe3f0; --muted:#8b98b3;
          --accent:#4f8cff; --ok:#2ecc71; --warn:#f1c40f; --err:#e74c3c; --run:#4f8cff; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.5 -apple-system,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif;
         background:var(--bg); color:var(--text); }
  header { padding:14px 20px; background:var(--panel); border-bottom:1px solid var(--line);
           display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
  header h1 { font-size:16px; margin:0; }
  input, select, button { background:var(--bg); color:var(--text); border:1px solid var(--line);
         border-radius:6px; padding:6px 10px; font-size:13px; }
  button { cursor:pointer; background:var(--accent); border-color:var(--accent); color:#fff; }
  button:hover { filter:brightness(1.1); }
  main { padding:20px; display:grid; grid-template-columns:420px 1fr; gap:20px; align-items:start; }
  @media (max-width:900px){ main { grid-template-columns:1fr; } }
  .panel { background:var(--panel); border:1px solid var(--line); border-radius:10px; }
  .panel h2 { font-size:13px; margin:0; padding:10px 14px; border-bottom:1px solid var(--line);
              color:var(--muted); font-weight:600; }
  .trace-row { padding:10px 14px; border-bottom:1px solid var(--line); cursor:pointer; }
  .trace-row:hover { background:#1d2740; }
  .trace-row .t-top { display:flex; justify-content:space-between; gap:8px; }
  .trace-row .t-id { font-family:ui-monospace,Consolas,monospace; font-size:12px; color:var(--accent); word-break:break-all; }
  .badge { font-size:11px; padding:1px 8px; border-radius:20px; border:1px solid var(--line); color:var(--muted); white-space:nowrap; }
  .badge.running{color:var(--run);border-color:var(--run);} .badge.success,.badge.completed{color:var(--ok);border-color:var(--ok);}
  .badge.failure,.badge.error,.badge.failed{color:var(--err);border-color:var(--err);}
  .badge.warning,.badge.waiting,.badge.suspended{color:var(--warn);border-color:var(--warn);}
  .badge.allow{color:var(--ok);border-color:var(--ok);}
  .t-meta { color:var(--muted); font-size:12px; margin-top:4px; }
  .tree { padding:12px 14px; }
  .span { margin:6px 0 6px 18px; border-left:2px solid var(--line); padding-left:12px; }
  .span>.span-head { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .span-head .name { font-weight:600; }
  .span-head .dur { font-family:ui-monospace,Consolas,monospace; font-size:12px; color:var(--muted); }
  .events { margin-top:6px; }
  .ev { display:flex; gap:8px; font-size:12px; padding:3px 0; border-bottom:1px dashed rgba(255,255,255,.05); }
  .ev .time { font-family:ui-monospace,Consolas,monospace; color:var(--muted); white-space:nowrap; }
  .ev .etype { color:var(--accent); }
  .ev .res { color:var(--muted); }
  .empty { padding:24px; color:var(--muted); text-align:center; }
  .detail-head { padding:12px 14px; border-bottom:1px solid var(--line); }
  .detail-head .d-id { font-family:ui-monospace,Consolas,monospace; color:var(--accent); word-break:break-all; }
  .detail-head .d-meta { color:var(--muted); font-size:12px; margin-top:4px; }
</style>
</head>
<body>
<header>
  <h1>🔍 全链路 Trace 查看</h1>
  <input id="session" placeholder="按 session_id 检索" style="min-width:320px">
  <input id="limit" type="number" value="30" min="1" max="500" style="width:80px" title="数量">
  <button onclick="load()">检索</button>
</header>
<main>
  <div class="panel">
    <h2>Trace 列表</h2>
    <div id="list" class="empty">输入 session_id 或直接点检索</div>
  </div>
  <div class="panel">
    <h2>链路详情</h2>
    <div id="detail" class="empty">点击左侧 trace 查看完整链路</div>
  </div>
</main>
<script>
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const badge = s => `<span class="badge ${esc(s||'')}">${esc(s||'—')}</span>`;
const fmt = ms => ms==null ? '—' : (ms>=1000 ? (ms/1000).toFixed(2)+'s' : ms.toFixed(1)+'ms');

async function api(path){
  const r = await fetch(path);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function load(){
  const session = document.getElementById('session').value.trim();
  const limit = document.getElementById('limit').value || 30;
  const q = new URLSearchParams({limit});
  if (session) q.set('session_id', session);
  const data = await api('/api/v1/traces?' + q.toString());
  const list = document.getElementById('list');
  if (!data.traces.length){ list.className='empty'; list.textContent='无匹配 trace'; return; }
  list.className=''; list.innerHTML = data.traces.map(t => `
    <div class="trace-row" onclick="showTrace('${t.trace_id}')">
      <div class="t-top"><span class="t-id">${esc(t.trace_id)}</span>${badge(t.status)}</div>
      <div class="t-meta">${esc(t.start_time||'')} · ${fmt(t.duration_ms)} · ${t.event_count} 事件 · ${esc(t.last_event||'')}</div>
      <div class="t-meta">${t.session_id?'session: '+esc(t.session_id):''} ${t.workflow_id?'· workflow: '+esc(t.workflow_id):''}</div>
    </div>`).join('');
}

function renderSpan(span){
  const events = span.event_types.map(et => `<div class="ev"><span class="etype">${esc(et)}</span></div>`).join('');
  const children = (span.children||[]).map(renderSpan).join('');
  return `<div class="span">
    <div class="span-head"><span class="name">${esc(span.last_event||span.span_id)}</span>
      ${badge(span.status)}<span class="dur">${fmt(span.duration_ms)}</span>
      <span class="dur">${esc((span.resource_type||[]).join(','))}</span>
      <span class="dur">${esc((span.resource_id||[]).join(','))}</span></div>
    <div class="events">${events}</div>${children}</div>`;
}

async function showTrace(id){
  const t = await api('/api/v1/traces/' + id);
  const d = document.getElementById('detail');
  d.className='';
  d.innerHTML = `<div class="detail-head"><div class="d-id">${esc(t.trace_id)}</div>
    <div class="d-meta">${esc(t.start_time||'')} · ${fmt(t.duration_ms)} · ${t.event_count} 事件 / ${t.span_count} span ${badge(t.status)}
      ${t.session_id?'· session '+esc(t.session_id):''} ${t.workflow_id?'· workflow '+esc(t.workflow_id):''}</div>
    </div><div class="tree">${(t.root_spans||[]).map(renderSpan).join('')}</div>`;
}
load();
</script>
</body>
</html>"""


async def _import_configured_remote_a2a_agents(app: FastAPI) -> None:
    """Import remote A2A agents from the persisted remote_services table.

    Also migrates legacy ``qa_routing_config`` (its ``classifier_endpoint``) into
    remote_services and clears the old key.
    """
    from harness.a2a.importer import import_remote_agents_from_endpoint

    db = app.state.database
    store = app.state.config_store

    # 1) Migrate legacy qa_routing_config → remote_services, then remove old key.
    legacy = await store.get("llm_settings", "qa_routing_config")
    if legacy and legacy.get("value"):
        try:
            legacy_cfg = json.loads(legacy["value"])
            legacy_endpoint = legacy_cfg.get("classifier_endpoint")
            if legacy_endpoint:
                existing = await db.fetch_one(
                    "SELECT id FROM remote_services WHERE endpoint = $1",
                    (legacy_endpoint,),
                )
                if not existing:
                    from datetime import datetime

                    from harness.utils.id_gen import generate_id

                    now = datetime.now(UTC).replace(tzinfo=None)
                    await db.execute(
                        "INSERT INTO remote_services (id, endpoint, token, timeout_seconds, skip_local, enabled, created_at, updated_at) "
                        "VALUES ($1, $2, $3, $4, 1, 1, $5, $5)",
                        (
                            generate_id(),
                            legacy_endpoint,
                            legacy_cfg.get("classifier_token", ""),
                            int(legacy_cfg.get("timeout_seconds", 300)),
                            now,
                        ),
                    )
                    await db.commit()
                    logger.info("Migrated legacy qa_routing_config endpoint %s → remote_services", legacy_endpoint)
            # 彻底清理旧配置
            await store.delete("llm_settings", "qa_routing_config")
        except Exception:
            logger.warning("qa_routing_config migration failed; leaving old key")

    # 2) Import every enabled remote service from remote_services.
    rows = await db.fetch_all(
        "SELECT id, endpoint, token, timeout_seconds, skip_local FROM remote_services WHERE enabled = 1"
    )
    if not rows:
        logger.info("No remote A2A services configured; skipping import")
        return
    for row in rows:
        endpoint = row["endpoint"]
        # master stays local (intent recognition + routing + execution); the
        # planner and the domain agents are imported remotely.
        skip_ids = {"master"} if bool(row.get("skip_local", 1)) else set()
        try:
            imported = await import_remote_agents_from_endpoint(
                endpoint=endpoint,
                registry=app.state.agent_registry,
                token=row.get("token") or "",
                timeout_seconds=float(row.get("timeout_seconds", 300)),
                # 只保留本地编排 agent（master/planner），专业子 agent 由远程导入。
                skip_agent_ids=skip_ids,
                # 远程产物内容回传本地 ArtifactService，供流水线/HITL 读取。
                artifact_service=app.state.artifact_service,
            )
            if imported:
                logger.info("Imported %s remote A2A agent(s) from %s: %s", len(imported), endpoint, imported)
        except Exception as exc:
            logger.warning("Remote A2A auto-import skipped for %s: %s", endpoint, exc)
