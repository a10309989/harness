# Agent 管理：添加远程 A2A 服务 + 移除 QA/Classifier Routing

> 文档编号: PRD-AGENT-REMOTE-A2A-20260811
> 状态: 草稿（待评审）
> 关联模块: Agent 管理页（`harness-front/src/pages/AgentsPage.tsx`）、Agent API（`harness/src/harness/api/routes/agents.py`）、Settings（`harness/src/harness/api/routes/settings.py`）、A2A 导入（`harness/src/harness/a2a/importer.py`）

## 1. 背景与问题

当前系统有两处与"远程 Agent"相关的功能，职责重叠且语义混乱：

### 1.1 现状问题

| 功能 | 现状 | 问题 |
|---|---|---|
| **Agent 管理页** | 只能**查看**本地 Agent 与远程 A2A Agent，可修改单个 Agent 的 LLM 配置 / MCP 绑定 | **无"添加远程 A2A 服务"入口**，无法通过 UI 接入新的远程 Agent 服务 |
| **Settings → QA / Classifier Routing** | 配置 `classifier_endpoint`（远程 A2A 服务地址）、`classifier_agent_id`、`qa_agent_id`、`threshold` 等 | **语义误导**：该配置实际承担"启动时从哪个远程 A2A 服务导入 Agent"的职责，却命名为"QA/Classifier"，用户无法理解其用途；`classifier_agent_id`/`qa_agent_id` 是预留的外部 QA 服务字段，当前并未部署 |

### 1.2 核心矛盾

- "远程 Agent 从哪导入"是**服务级**配置，却被塞在语义为"QA 分类"的入口下；
- Agent 管理页作为 Agent 的**管理面**，却缺少"接入远程服务"这一最基础的能力。

## 2. 目标

1. **Agent 管理页新增"添加远程 A2A 服务"**：通过 UI 输入 A2A 服务端点，导入该服务的 Agent 卡片并注册到本地，实现远程 Agent 的动态接入。
2. **Agent 管理定位为远程服务管理面**：除本地编排 agent（master/planner）外，**不支持新增/配置本地 agent**；新增 Agent 唯一来源是远程 A2A 服务。
3. **远程 Agent 心跳能力上报**：远程 Agent 通过心跳上报能力/Skill/状态到 Agent Hub，Agent 管理页展示。
4. **移除 / 重命名 QA / Classifier Routing**：把"远程 Agent 导入源"的配置迁移到 Agent 管理的"远程服务"概念下，消除语义混淆；移除预留但未使用的 `classifier_agent_id` / `qa_agent_id` 等字段。

## 3. 需求详述

### 3.1 Agent 管理页：新增 Agent（逐个配置 endpoint）

**架构定位**：**每个 Agent 独立部署** —— 每个 agent 跑在独立的 IP + 端口（如 requirements 在 `http://10.0.0.5:8101/a2a`、log_analyst 在 `http://10.0.0.6:8102/a2a`）。Agent 管理作为**管理面**，新增 Agent 时**逐个填写 endpoint** 接入；除本地编排 agent（`master`/`planner`）外，不支持新增/配置本地 agent。

**功能入口**：Agent 管理页新增"新增 Agent"按钮。

**交互流程**：
1. 点击"新增 Agent"。
2. 弹出对话框/表单，输入：
   - **Agent ID（可选）**：如留空，从远程 agent 卡片获取
   - **端点（Endpoint）**：必填，该 Agent 独立服务的 A2A JSON-RPC 地址（如 `http://10.0.0.5:8101/a2a`）
   - **认证 Token（可选）**：若该服务需要 Bearer 认证
   - **超时（秒）**：可选，默认 300
3. 点击"接入"：
   - 后端拉取该端点的 `/.well-known/agent-cards`，确认该端点提供的 Agent
   - 若端点提供单个 Agent → 注册该 Agent；若提供多个 → 全部注册（或由 Agent ID 过滤）
   - 注册为 `A2ARemoteAgentAdapter` 到本地 AgentRegistry
   - 返回接入结果（成功/失败 + Agent 列表）
4. 接入成功后，Agent 列表刷新显示新 Agent（带 `remote` 标记 + 端点信息 + 独立 endpoint）。

**失败处理**：
- 端点不可达 / 非 A2A 服务 → 返回明确错误信息（如"无法连接端点"、"未找到 Agent 卡片"）。
- 同名冲突（已存在同 id Agent）→ 提示覆盖或跳过。

**持久化**：每个 Agent 的 `{endpoint, token, timeout}` 独立持久化（`remote_services` 表或按 agent 粒度），服务重启后自动重新接入。


### 3.2 远程 Agent 心跳能力上报（Agent Hub）

**背景**：远程 Agent 卡片（`AgentCard`）有 `capabilities` 字段，但当前 8101 上报的是**空**（仅 `runtime: react` 元数据）。需通过**心跳机制**让远程 Agent 主动上报能力/Skill，供 Agent 管理页展示。

**设计**：
1. **心跳协议**：远程 A2A 服务提供 `GET /a2a/agent-status`（或复用 `agent.discover`），返回每个 Agent 的：
   - `agent_id` / `name` / `version`
   - `capabilities`: `list[AgentCapability]`（name/description）
   - `skills`: 已绑定/可用的 Skill 列表（name/category/description）
   - `mcp_servers`: 远程侧启用的 MCP（仅展示，不本地管理）
   - `runtime` / `status`（online/offline/busy）
   - 时间戳
2. **心跳频率**：本地后端按可配置间隔（默认 30s）轮询已配置的远程服务端点，刷新 Agent Hub 中每个 Agent 的状态与能力。
3. **Agent Hub 展示**：Agent 管理页的远程 Agent 详情区展示：
   - 实时状态（online/offline，带最后心跳时间）
   - **Capabilities** 列表（能力）
   - **Skills** 列表（远程 Agent 使用了哪些 Skill）
   - 远程 MCP 仅展示（不提供本地绑定）
4. **下线检测**：超过 N 个心跳周期未上报 → 标记 offline，Agent 管理页显示离线态。

**本地 Agent 的心跳/能力来源**：
- 本地 Agent 的 `bind_skills` + 能力从本地 SkillRegistry / Agent 配置直接读取，不走心跳。
- 本地 Agent 的 MCP 绑定由本地管理（现有 `PUT /agents/{id}/mcp`），与远程展示互不影响。

### 3.3 云端 vs 本地 MCP 冲突策略

**决策**：**Agent MCP 由本地管理**。本地 Agent 的 MCP 绑定在本地配置（现有）；远程 Agent 的 MCP 仅展示不本地绑定。因此：
- **无"云端 vs 本地 MCP 绑定冲突"**——本地只管本地 Agent 的 MCP；远程 Agent 的 MCP 归属远程服务。
- Agent 管理页展示远程 MCP 仅为只读参考，不参与本地绑定合并。

### 3.4 远程 Agent Skill 上报（自我管理）

**决策**：**远程 Agent 的 Skill 由远程自我管理**，本地仅**展示上报的 Skill 能力**，不提供本地绑定/管理。

- 远程 Agent 通过心跳上报其已绑定/可用的 `skills` 列表（name/category/description）。
- Agent 管理页远程 Agent 详情**只读展示** Skills（与 Capabilities 并列），无编辑入口。
- 本地 Agent 的 Skills 绑定仍在本地管理（现有 `bind_skills`）。
- 这样 MCP 与 Skill 的归属边界清晰：**MCP 本地管（远程只展示）、Skill 各自管（远程自我管理，本地只展示上报能力）**。


**持久化**：
- 已添加的远程服务端点列表需**持久化**（SQLite / 现有 `llm_settings` 或新建配置表），服务重启后自动重新导入。
- 支持"移除远程服务"（删除配置并注销对应 Agent）。

### 3.5 移除 / 重命名 QA / Classifier Routing

**从 Settings 页移除**：删除 Settings 页的 "QA / Classifier Routing" 区块（`SettingsPage.tsx` 中 `qaRouting` 相关 UI + 保存逻辑）。

**后端调整**：
- 移除 `settings.py` 的 `get_qa_routing_config` / `save_qa_routing_config` 路由及其 `DEFAULT_QA_ROUTING_CONFIG`。
- 移除 `qa_routing_config` 的读取与消费（`app.py` 的 `_import_configured_remote_a2a_agents`）。
- **迁移**：现有 `classifier_endpoint` 配置迁移到新的"远程服务"配置（Agent 管理新增的持久化字段），启动时从持久化的远程服务列表导入 Agent。

**兼容性**：
- 已存储的 `qa_routing_config` 数据：启动时若存在旧配置，迁移其 `classifier_endpoint` 到新结构，并清理旧字段。

### 3.6 后端 API 变更

**新增**：
- `POST /api/v1/agents/remote` — 新增 Agent（body: `{endpoint, token?, timeout_seconds?, agent_id?}`），拉取该端点的 agent 卡片注册，持久化，返回接入的 Agent 列表
- `GET /api/v1/agents/remote` — 列出已接入的远程 Agent（endpoint/token/timeout + 心跳状态）
- `DELETE /api/v1/agents/remote/{id}` — 移除远程 Agent（注销 + 删配置）

**移除**：
- `GET/PUT /api/v1/settings/qa-routing-config`（或现有对应路由）

### 3.7 权限

- 新增/移除远程 Agent：`agent:manage`（admin/operator）。
- 查看远程 Agent/心跳：`agent:read`。


## 4. 已确认设计决策

1. **每个 Agent 独立部署**：每个 agent 跑独立 IP+端口；Agent 管理逐个配置 endpoint 接入。
2. **持久化**：按 agent 粒度持久化 `{endpoint, token, timeout}`（`remote_services` 表，每行一个 agent 的接入信息），重启后自动重新接入。
3. **移除 Agent 时**：**注销**该 Agent（从 AgentRegistry 移除 + 清除接入配置）。
4. **权限**：新增 `agent:manage` 权限（新增/移除远程 Agent），查看用 `agent:read`；admin/operator 授予。
5. **旧 `qa_routing_config` 数据**：迁移其 `classifier_endpoint` 到新结构后**彻底清理**。

**统一入库 + Planner 动态规划**：Agent 管理作为所有 Agent（本地 + 远程）的**统一入库**来源。远程 Planner 通过 A2A 从注册表获取可用 Agent（排除 master/planner）并动态编排生成 DAG；测试生命周期请求仍走固定 DAG，非生命周期请求走 planner 动态规划。

## 5. 非目标（本期不做）

- 远程 Agent 的在线/离线健康检查（仅展示导入状态）。
- 远程 Agent 的卡片编辑（仅接入/移除）。
- 多租户隔离。

## 6. 验收标准

1. 在 Agent 管理页点击"新增 Agent"，输入某个独立部署的 agent 端点（如 `http://10.0.0.5:8101/a2a`），接入该 Agent，列表显示 `remote` 标记 + 端点。
2. 多个 agent 各用独立 endpoint 接入（requirements 一个、log_analyst 一个等），各自独立注册。
3. 接入后，流水线仍可正常路由到这些远程 Agent（端到端跑通）。
4. 重复接入同一端点 → 提示已存在 / 幂等处理。
5. Settings 页不再显示 "QA / Classifier Routing"；旧 `qa_routing_config` 的 `classifier_endpoint` 迁移后彻底清理（`grep qa_routing_config` 无代码引用）。
6. 服务重启后，已接入的远程 Agent（按 endpoint 持久化）自动重新接入，Agent 列表恢复。
7. 移除远程 Agent → 注销（Registry 移除），列表不再显示。
8. `remote_services` 表持久化正确（每行一个 agent：endpoint/Token/超时）。
9. 非 master/planner 用户无 `agent:manage` 权限时，新增/移除返回 403。
10. Planner 动态规划：新入库的远程 Agent 出现在 planner 的可用列表中（`registry.list_agents()` 排除 master/planner）。
10. **心跳上报**：远程 Agent 心跳接口返回能力/Skill/状态；Agent 管理页远程 Agent 展示实时状态 + Capabilities + Skills（每 ~30s 刷新）。
11. **下线检测**：远程服务停止后，Agent 管理页在 N 个心跳周期内将其标记为 offline。
12. **MCP 归属**：本地 Agent 的 MCP 本地绑定正常；远程 Agent 的 MCP 仅展示不提供本地绑定。
13. 前端 `tsc` + `vite build` 通过；后端现有测试通过。


## 7. 测试验证（自动化测试）

后续基于本文档实现后，执行以下自动化测试验证：

- **单元测试**：`agents.py` 新增 POST/GET/DELETE remote-services 路由的校验、幂等、404/400 分支；`agent:manage` 权限校验（403）。
- **集成测试**：添加远程服务（mock endpoint 或真实 8101）→ 验证 Agent 注册；移除 → **级联注销**；重复添加 → 幂等。
- **心跳测试**：远程服务上报能力/Skill/状态 → 本地 Agent Hub 记录并展示；停止服务 → 标记 offline（N 周期内）。
- **迁移测试**：预置旧 `qa_routing_config` → 启动迁移到 `remote_services` → 旧字段清理；`grep qa_routing_config` 无代码引用。
- **Planner 动态规划**：入库远程 Agent 后，`registry.list_agents()` 含新 Agent，planner 可用列表包含之。
- **端到端**：通过 UI 添加远程服务后，跑一次完整流水线验证路由到远程 Agent。
- **回归**：确认 Settings 移除 QA/Classifier Routing 后无遗留引用。

## 8. 关联文件（预期改动）

| 文件 | 改动 |
|---|---|
| `harness-front/src/pages/AgentsPage.tsx` | 新增"添加远程 A2A 服务"UI + 调用新 API |
| `harness-front/src/pages/SettingsPage.tsx` | 移除 QA/Classifier Routing 区块 |
| `harness/src/harness/api/routes/agents.py` | 新增 remote-services CRUD 路由 |
| `harness/src/harness/api/routes/settings.py` | 移除 qa-routing-config 路由 |
| `harness/src/harness/api/app.py` | 启动时从 `remote_services` 表导入（替代 qa_routing_config）+ 旧配置迁移 |
| `harness/src/harness/a2a/importer.py` | 复用 `import_remote_agents_from_endpoint` |
| `harness/src/harness/db/postgres_migrations.py` + 新迁移 | 新建 `remote_services` 表 |
| `harness/src/harness/api/config_store.py` | 远程服务配置持久化到 `remote_services` 表 |
| `harness/src/harness/security/permissions.py` | 新增 `agent:manage` 权限 + 角色授予 |
| `harness/src/harness/api/wire_agents.py` / `core/planner.py` | 确认 planner 从 registry 动态取可用 Agent（无需改动，验证即可） |
| `harness/src/harness/api/routes/agents.py` | 新增远程 Agent 心跳状态 / 能力 / Skills 查询（Agent Hub 读取） |
| `harness/src/harness/a2a/heartbeat.py`（新增） | 心跳轮询器：定时拉取远程服务 agent-status，更新 Agent Hub 状态/能力 |
| `harness/src/harness/core/registry.py` | AgentRegistry 记录远程 Agent 心跳状态/能力/Skills |
| `harness-front/src/pages/AgentsPage.tsx` | 远程 Agent 详情展示实时状态 + Capabilities + Skills（每 30s 刷新） |
| `harness-agents/src/a2a/bridge.py` | 远程端新增 `GET /a2a/agent-status`（返回能力/Skills/状态），供心跳拉取 |

## 9. 已确认决策回顾

1. **每个 Agent 独立部署**：独立 IP+端口；Agent 管理逐个配置 endpoint 接入
2. 持久化：`remote_services` 表（每行一个 agent 的接入信息）
3. 移除：注销对应 Agent
4. 权限：新增 `agent:manage`
5. 旧数据：迁移后彻底清理
6. Agent 管理作为统一入库，planner 基于入库 Agent 动态 DAG 规划
7. **Agent MCP 由本地管理**：本地 Agent MCP 本地绑定；远程 Agent MCP 仅展示（不本地管理，无云端/本地冲突）
8. **远程 Agent 心跳上报到 Agent Hub**：远程 Agent 通过心跳上报能力/Skill/状态，Agent 管理页展示
9. **远程 Agent Skill 自我管理**：远程 Agent 的 Skill 由远程管理，本地仅展示上报的 Skill 能力（不绑定/不管理）；本地 Agent 的 Skill 仍在本地管理
10. **无本地 Agent 新增**：除 master/planner 外，Agent 管理不支持新增/配置本地 agent；新增 Agent 通过逐个配置独立 endpoint 接入
