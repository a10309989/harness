# Enterprise AI Test Harness

企业级 AI 测试编排与自动化执行平台。

Harness 由本地 `Master Agent` 负责意图识别、路由和流程编排，需求分析、测试策略、测试用例、脚本生成、测试执行和诊断等专业能力通过远程 A2A Agent 独立部署和接入。

## 核心能力

- 需求文档分析与结构化拆解
- 测试策略、测试用例和自动化脚本生成
- UI 自动化测试执行、逐步截图和报告产物
- Temporal 工作流、审批、重试和断点恢复
- PostgreSQL 原生持久化与版本化迁移
- Redis Streams 实时事件、消费重试和 DLQ
- Artifact 版本管理与 MinIO 迁移支持
- Docker 隔离 Runner 与执行证据采集
- 远程 A2A Agent 注册、发现、路由和调用
- OTel Trace、审计、RBAC、限流和网关能力

## 架构边界

```text
用户 / API / UI
       |
       v
Master Agent（本地：分类、路由、编排）
       |
       +--> Planner Agent（远程 A2A）
       +--> Requirements Agent（远程 A2A）
       +--> Test Strategy Agent（远程 A2A）
       +--> Test Case Agent（远程 A2A）
       +--> Script Agent（远程 A2A）
       +--> Execution Agent（远程 A2A）
       +--> Diagnosis Agent（远程 A2A）

Temporal --> 长流程、审批、重试、恢复
PostgreSQL --> 业务数据、工作流投影、审计
Redis --> 实时事件、消费通知、DLQ
Docker Runner --> 隔离执行测试脚本
Artifact Store --> 策略、用例、脚本、截图、报告
```

## 技术栈

- Python 3.11+、FastAPI、Pydantic
- Temporal、PostgreSQL、Redis Streams
- Docker、MinIO、OpenTelemetry
- A2A JSON-RPC、LangGraph
- pytest、Playwright / Selenium 生态

## 目录结构

```text
src/harness/          核心 API、Master、工作流、存储和执行服务
config/               Agent、LLM、MCP 和路由配置
postgres/migrations/  PostgreSQL 版本化迁移
deploy/               Harness、Temporal、Gateway、Runner 部署文件
docs/                 架构、运维、Agent 和迁移设计
tests/                单元、集成和工作流测试
```

## 本地启动

安装依赖：

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -e ".[dev]"
```

启动基础设施：

```bash
docker compose --env-file deploy/temporal/.env \
  -f deploy/temporal/docker-compose.yaml up -d
```

启动 API：

```bash
uvicorn harness.api.app:create_app --factory --reload
```

远程 Agent 需要通过 Agent 管理 API 或持久化的 `remote_services` 配置接入 A2A Endpoint。没有远程专业 Agent 时，Master 仍可提供分类和路由，但无法执行专业测试生命周期节点。

## 验证

```bash
python -m compileall -q src tests
pytest -q
```

## 相关文档

- `docs/architecture.md`：整体架构
- `docs/agent-extension-standard.md`：远程 Agent 扩展规范
- `docs/workflow-platform-design.md`：工作流平台设计
- `docs/enterprise-ops-runbook.md`：企业级运维手册
- `deploy/temporal/README.md`：Temporal 本地部署

## License

License information is maintained by the project owner.
