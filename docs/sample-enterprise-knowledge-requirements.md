# 企业级 Harness Agent 平台知识库管理需求文档

## 1. 文档信息

| 字段 | 内容 |
| --- | --- |
| 文档名称 | 企业级 Harness Agent 平台知识库管理需求 |
| 版本 | v0.1 |
| 状态 | 草案 |
| 编写日期 | 2026-07-30 |
| 适用范围 | 单租户内网部署的 Harness Agent 平台 |
| 目标用户 | 测试平台管理员、测试工程师、运维工程师、Agent 开发者 |

## 2. 背景

当前 Harness Agent 平台已经具备需求分析、测试策略生成、需求转用例、用例转脚本、测试执行、诊断与日志分析等 Agent 能力。随着 Agent 数量增加，平台需要统一管理需求文档、测试资产、运维手册、日志样本、历史故障和 Agent 生成产物，避免各 Agent 重复维护知识、引用来源不可追踪、权限边界不清晰等问题。

因此，需要建设一个企业级知识库管理能力，作为所有 Agent 的统一知识底座。

## 3. 建设目标

1. 平台应提供统一知识目录，支撑需求、架构、接口、测试、自动化、运维、日志、故障复盘和 Agent 产物管理。
2. Agent 不直接访问底层数据库或向量库，应通过统一知识服务检索和写入知识。
3. 不同 Agent 应具备独立的知识访问 Profile，用于限定可访问目录、检索策略和默认返回数量。
4. Agent 生成的结构化产物应可回写知识库，形成从需求到诊断的闭环。
5. 每次知识检索应记录审计事件，包含 traceId、Agent、查询内容、命中结果和过滤结果。
6. UI 应提供目录浏览、文档上传、内容粘贴入库和目录内搜索能力。

## 4. 术语定义

| 术语 | 说明 |
| --- | --- |
| 知识目录 | 按领域划分的知识集合，例如需求知识、测试资产、运维手册 |
| 知识文档 | 一个完整知识来源，例如 PRD、Runbook、日志片段、测试策略 |
| 知识切片 | 从知识文档中切分出的可检索内容片段 |
| Agent Knowledge Profile | Agent 专属知识访问配置 |
| Artifact 回写 | 将 Agent 生成产物索引到知识库，供后续 Agent 使用 |
| 检索审计 | 对每次知识检索行为进行留痕 |

## 5. 用户角色

| 角色 | 权限诉求 |
| --- | --- |
| 平台管理员 | 管理知识目录、配置 Agent Profile、查看检索审计 |
| 测试工程师 | 上传需求、策略、用例、脚本文档并检索 |
| 运维工程师 | 上传 Runbook、日志样本、故障复盘并检索 |
| Agent 开发者 | 为新 Agent 配置知识目录和检索策略 |
| 审计人员 | 查看知识使用记录和产物引用链 |

## 6. 知识目录需求

平台默认应内置以下知识目录：

| 目录标识 | 中文名称 | 内容范围 | 主要使用 Agent |
| --- | --- | --- | --- |
| requirements | 需求知识 | PRD、用户故事、验收标准、业务规则 | 需求分析 Agent、测试策略 Agent |
| architecture | 架构知识 | 架构图、ADR、接口设计、链路说明 | 测试策略 Agent、诊断 Agent |
| api_specs | 接口契约 | OpenAPI、Proto、Postman、Mock 契约 | 用例转脚本 Agent、执行 Agent |
| test_assets | 测试资产 | 测试策略、测试用例、测试数据、覆盖矩阵 | 需求转用例 Agent、执行 Agent |
| automation_code | 自动化代码 | 脚本模板、PageObject、框架规范 | 用例转脚本 Agent |
| runbooks | 运维手册 | SOP、发布回滚、故障处理手册 | 执行 Agent、诊断 Agent、QA Agent |
| observability | 可观测数据 | 日志、Trace、Metrics、告警样本 | 诊断及日志分析 Agent |
| incidents | 故障复盘 | 历史缺陷、事故复盘、根因记录 | 测试策略 Agent、诊断 Agent |
| agent_artifacts | Agent 产物 | 需求拆解、测试策略、用例、脚本、执行报告、诊断报告 | 全部 Agent |

## 7. 功能需求

### 7.1 知识目录管理

1. 系统启动后应自动创建默认知识目录。
2. UI 应展示全部知识目录，即使目录下暂无文档也应可见。
3. 用户应可以查看目录名称、说明和目录标识。
4. 管理员后续应可以新增、编辑、停用自定义知识目录。

### 7.2 文档上传与入库

1. 用户应可以选择目标知识目录上传文档。
2. 用户应可以直接粘贴文本内容入库。
3. 平台应支持上传 `.txt`、`.md`、`.json`、`.yaml`、`.yml`、`.log`、`.csv` 格式。
4. 入库时用户应填写文档标题。
5. 入库时用户可选填写来源 URI，例如 Confluence、Git、文件来源或运维系统链接。
6. 平台应对文档内容进行切片，并生成可检索的知识片段。
7. 入库成功后应返回文档 ID、切片数量和内容摘要校验值。

### 7.3 知识检索

1. 用户应可以在指定知识目录内搜索。
2. Agent 应可以基于自己的 Knowledge Profile 发起检索。
3. 检索结果应包含标题、目录、来源类型、来源 URI、内容片段、分数和 Artifact 引用。
4. 检索应支持关键词检索、语义检索和混合检索三种策略。
5. 当前阶段可先实现关键词或轻量混合检索，后续接入 pgvector、OpenSearch 或 Elasticsearch。

### 7.4 Agent Knowledge Profile

每个 Agent 应配置以下字段：

| 字段 | 说明 |
| --- | --- |
| agent_id | Agent 唯一标识 |
| collections | Agent 可访问知识目录 |
| strategy | 默认检索策略 |
| limit | 默认返回数量 |
| require_citations | 是否要求结果附带引用 |
| write_back_artifacts | 是否允许产物自动回写 |

默认配置建议如下：

| Agent | 默认知识目录 |
| --- | --- |
| 需求分析 Agent | requirements、architecture、agent_artifacts |
| 测试策略 Agent | requirements、architecture、incidents、agent_artifacts |
| 需求转用例 Agent | requirements、test_assets、agent_artifacts |
| 用例转脚本 Agent | api_specs、automation_code、test_assets、agent_artifacts |
| 执行测试 Agent | test_assets、runbooks、automation_code |
| 诊断及日志分析 Agent | observability、incidents、runbooks、agent_artifacts |
| QA 问答 Agent | requirements、architecture、runbooks、agent_artifacts |

### 7.5 Artifact 回写

1. 需求分析 Agent 输出的结构化需求应回写到 `agent_artifacts`。
2. 测试策略 Agent 输出的测试策略应回写到 `agent_artifacts` 和 `test_assets`。
3. 需求转用例 Agent 输出的用例集应回写到 `test_assets`。
4. 用例转脚本 Agent 输出的脚本应回写到 `automation_code`。
5. 执行测试 Agent 输出的执行报告和日志索引应回写到 `agent_artifacts` 和 `observability`。
6. 诊断 Agent 输出的根因分析和修复建议应回写到 `incidents` 和 `agent_artifacts`。
7. 回写前应确保 Artifact 已经持久化，并记录 Artifact 与知识文档的关联关系。

### 7.6 检索审计

每次检索应记录以下信息：

| 字段 | 说明 |
| --- | --- |
| trace_id | 链路追踪 ID |
| tenant_id | 租户 ID |
| agent_id | 发起检索的 Agent |
| query | 查询内容 |
| collection_names | 实际检索目录 |
| result_count | 返回结果数量 |
| filtered_count | 权限过滤数量 |
| source_refs | 命中的知识来源 |
| created_by | 发起人 |
| created_at | 检索时间 |

## 8. Agent 使用场景

### 8.1 需求分析场景

用户上传 PRD 到 `requirements` 目录。需求分析 Agent 检索历史需求和架构知识，输出结构化需求、验收标准、风险点和可测性分析，并将结果保存为 Artifact 后回写知识库。

### 8.2 测试策略场景

测试策略 Agent 读取结构化需求、历史故障和架构文档，生成测试范围、测试类型、优先级、覆盖矩阵和风险应对策略。

### 8.3 需求转用例场景

需求转用例 Agent 读取需求分析产物、测试策略和历史用例，生成结构化测试用例，包括前置条件、步骤、断言、优先级和需求覆盖关系。

### 8.4 用例转脚本场景

用例转脚本 Agent 读取测试用例、接口契约和自动化脚本模板，生成可执行脚本，并记录依赖、运行参数和维护说明。

### 8.5 执行测试场景

执行测试 Agent 读取脚本、测试数据和 Runbook，提交 Runner 执行任务，生成执行报告、日志索引和失败摘要。

### 8.6 诊断及日志分析场景

诊断 Agent 读取执行日志、Trace、历史故障和运维手册，输出根因分析、证据链、修复建议和回归建议。

## 9. 非功能需求

### 9.1 权限与安全

1. 知识检索应遵守租户、项目、角色和密级过滤。
2. Agent 只能访问 Profile 中声明的知识目录。
3. 高敏感来源应支持 ACL 标签过滤。
4. 所有上传、检索和回写行为应可审计。

### 9.2 可观测性

1. 知识检索应透传 traceId。
2. 应统计检索延迟、命中数量、空结果率和失败率。
3. 应支持按 Agent、目录和用户维度查看知识使用情况。

### 9.3 可扩展性

1. 检索引擎应支持从当前轻量检索平滑升级到 pgvector。
2. 日志和大规模文本检索应支持接入 OpenSearch 或 Elasticsearch。
3. 文档来源应支持扩展 Confluence、Git、对象存储、日志平台和工单系统。

## 10. 验收标准

1. 用户打开知识库页面后，可以看到默认 9 个企业知识目录。
2. 用户可以选择任一目录上传文件并成功入库。
3. 用户可以在指定目录内搜索刚上传的内容。
4. Agent 调用知识检索时，会自动应用 Agent Knowledge Profile。
5. Artifact 可以被索引到 `agent_artifacts` 目录。
6. 每次检索都会写入检索审计记录。
7. 后端测试通过，且前端 TypeScript 构建无错误。

## 11. 待后续增强

1. 接入 pgvector，实现真正的向量语义检索。
2. 接入 OpenSearch 或 Elasticsearch，承接日志和大规模精确检索。
3. 增加知识目录管理 UI，包括新增、编辑、停用和权限配置。
4. 增加文档版本管理和增量索引。
5. 增加知识质量评分、过期提醒和重复文档检测。
6. 增加 Agent 产物自动回写策略配置。
