# Query Pilot MCP 与 Skill 改造

更新时间：2026-11-03

## 1. 目标与边界

Query Pilot 将 MCP 用作跨客户端的结构化工具协议，将 Skill 用作按需加载的业务分析工作流。两者都不是新的安全边界：认证、租户、数据源权限、SQL AST 校验、审批、脱敏、Gateway 和审计仍由现有 Query Pilot 服务负责。

本阶段不提供裸 `query.execute` MCP Tool。`query.plan` 只校验计划和候选只读 SQL，不访问数据库；真正执行必须回到现有查询接口。

## 2. 当前架构

```text
MCP Client / Query Pilot HTTP Client
  -> Bearer JWT / API Key / 本地开发身份
  -> Skill Router（确定性关键词与优先级）
  -> MCP Tool / Skill API
  -> ReadOnlyIntegrationService
  -> RBAC / Tenant / Datasource / Schema Policy / Audit
```

Skill 是声明式数据，不包含可执行 Python、任意系统提示词、Shell 命令或远程资源。每个 Skill 固定声明版本、适用意图、允许工具、角色和工作步骤。

## 3. 当前能力

内置 Skill：

- `schema-exploration`
- `sales-analysis`
- `receivables-analysis`
- `trend-analysis`
- `sql-diagnostics`

HTTP API：

```http
GET  /api/v1/skills
POST /api/v1/skills/route
```

管理员发布 API（要求 `manage` 权限）：

```http
GET  /api/v1/skills/admin/catalog
GET  /api/v1/skills/admin/versions
POST /api/v1/skills/admin/versions
POST /api/v1/skills/admin/versions/{version_id}/canary
POST /api/v1/skills/admin/versions/{version_id}/publish
POST /api/v1/skills/admin/versions/{version_id}/rollback
POST /api/v1/skills/admin/preview
```

标准 MCP Tools：

- `schema.list`
- `query.plan`
- `task.status`
- `result.get`
- `skill.route`

MCP Resource Template：

```text
querypilot://skills/catalog/{tenant_id}
```

读取 Skill 目录时，路径中的 `tenant_id` 必须与 Bearer 身份一致。

外部 MCP Server 注册表（仅管理员）：

```http
GET    /api/v1/mcp-servers
POST   /api/v1/mcp-servers
PATCH  /api/v1/mcp-servers/{server_id}
POST   /api/v1/mcp-servers/{server_id}/health
POST   /api/v1/mcp-servers/{server_id}/invoke
DELETE /api/v1/mcp-servers/{server_id}
```

注册表保存 `streamable-http` Endpoint、环境变量凭据引用、精确 Tool allowlist 和运行时治理参数。不接受 stdio 命令、内联 Token、任意 Header 或 `*` 通配符。远程 Endpoint 必须使用 HTTPS；HTTP 只允许 `localhost` 或字面量回环 IP。注册、健康检查和调用均要求 `manage` 权限、按租户过滤并写入脱敏审计事件。

示例请求只引用部署环境中的密钥，不包含密钥值：

```json
{
  "name": "Finance MCP",
  "transport": "streamable-http",
  "endpoint": "https://mcp.example.com/mcp",
  "auth_env_ref": "FINANCE_MCP_TOKEN",
  "allowed_tools": ["schema.list", "report.read"],
  "enabled": true,
  "read_only_only": true
}
```

响应中的 `credential_configured` 只说明当前进程能否解析该环境变量，不返回环境变量的值。每次健康检查或调用都会重新解析 DNS、发现远端工具并核验精确 allowlist。调用只放行明确声明 `readOnlyHint=true` 且未声明 `destructiveHint=true` 的工具；重复工具名、缺少只读声明和交互式 `input_required` 均失败关闭。

### 外部调用治理

外部调用使用官方 Python `mcp==2.3.0` Streamable HTTP 客户端，并执行以下限制：

- Bearer Token 只从 `auth_env_ref` 指向的进程环境变量读取，不写入数据库、响应或审计。
- 禁用环境代理、禁止重定向，并限制参数、结果、SSE 单事件、工具数量和分页数量。
- 每个租户和 Server 独立执行并发、每日调用次数和熔断治理；单机默认使用内存，多实例部署应使用 Redis。
- 默认禁止私网目标；管理员可显式设置 `allow_private_network=true`。即使允许私网，也始终拒绝 link-local、metadata、multicast、unspecified 和 reserved 地址。
- 普通工具调用不自动重试，避免对远端产生重复副作用。健康检查不消耗每日调用次数。
- 审计只记录 Server ID、工具名、耗时和错误码，不记录参数、结果、Token 或完整 Endpoint。

全局配置示例：

```dotenv
MCP_RUNTIME_BACKEND=memory
MCP_QUEUE_TIMEOUT_SECONDS=0.25
MCP_MAX_ARGUMENTS_BYTES=65536
MCP_MAX_RESULT_BYTES=1048576
MCP_MAX_SSE_EVENT_BYTES=1048576
MCP_MAX_DISCOVERED_TOOLS=200
MCP_MAX_TOOL_PAGES=10
```

`MCP_RUNTIME_BACKEND=redis` 时必须同时配置 `REDIS_URL`。这里的“每日预算”按调用次数计，不是货币成本。

调用请求示例：

```json
{
  "tool_name": "report.read",
  "arguments": {
    "region": "华东"
  }
}
```

边界说明：`readOnlyHint` 是远端 Server 的声明，不能从技术上证明实现绝对无副作用；DNS 在连接前复核但仍存在 DNS rebinding 残余风险。生产环境应在出口代理、防火墙、服务身份、目标证书和网络策略层继续限制可访问目标。

### 管理页面

管理员登录后可从左侧“`MCP 集成`”进入 `/admin/mcp`。页面支持：

- 注册、编辑、启停和删除当前租户的外部 MCP Server。
- 配置 Endpoint、凭据环境变量名、精确 Tool allowlist、超时、并发、每日次数和熔断参数。
- 查看凭据是否已注入、健康状态、延迟、错误码、允许工具和远端发现工具。
- 手动执行健康检查，以及对“已发现且位于白名单”的工具进行一次显式只读诊断调用。

页面不提供 Token、API Key 或密码输入框。诊断参数必须是 JSON 对象，诊断结果只保存在当前页面内存中；停用或非健康 Server 不允许从页面发起工具调用。viewer 和 analyst 看不到入口，直接访问管理路由会返回工作台，后端仍独立要求 `manage` 权限。

### Skill 发布页面

管理员可从左侧“`Skill 发布`”进入 `/admin/skills`：

- 基于当前有效声明创建新的语义版本草稿，草稿不会影响运行请求。
- 以 1%～100% 配置灰度；同一租户、用户、Skill 和版本使用稳定哈希，重复请求命中结果一致。
- 预览指定用户的稳定桶、灰度版本、当前 active 和最终选中版本。
- 正式发布时切换唯一 active；首次发布会保存内置版本基线。
- 回滚通过重新激活历史版本完成，不删除新版本和审计历史。

版本状态为 `draft -> canary -> active -> retired`。未命中 canary 时回退 active；没有租户 active 时回退内置版本。发布、灰度和回滚均按租户隔离并写入脱敏审计。普通 Skill API、路由工具和 MCP Skill Resource 共用同一解析服务，不会出现管理页与运行时版本分叉。

发布页面可版本化编辑说明、触发词、适用意图和工作步骤；后端仍会完整验证 SkillDefinition，并限制各声明长度。工具、角色和只读策略不由该编辑器修改。允许工具只可能是 `schema.list`、`query.plan`、`task.status`、`result.get`，不接受 `query.execute`、Python、Shell、远程资源或任意系统提示词。

## 4. 启动方式

安装依赖后，在项目根目录执行。下面命令为 Windows CMD。

### 本地 MCP stdio

本地 `.env` 已配置 `API_KEYS` 时，推荐直接运行安全启动脚本。脚本读取第一枚 Key 注入子进程，但不会把 Key 打印到终端：

```cmd
scripts\start_mcp_stdio.cmd
```

需要临时使用其他访问令牌时，也可以手动启动：

```cmd
set QUERY_PILOT_MCP_TOKEN=<access-token-or-api-key>
.venv\Scripts\python.exe scripts\run_mcp_server.py --transport stdio
```

MCP 客户端配置示例：

```json
{
  "mcpServers": {
    "query-pilot": {
      "command": "D:\\path\\to\\QueryPilot\\.venv\\Scripts\\python.exe",
      "args": ["D:\\path\\to\\QueryPilot\\scripts\\run_mcp_server.py", "--transport", "stdio"],
      "env": {
        "QUERY_PILOT_MCP_TOKEN": "<access-token-or-api-key>"
      }
    }
  }
}
```

不要把真实 Token 提交到项目文件或客户端共享配置模板。

### Streamable HTTP

```cmd
.venv\Scripts\python.exe scripts\run_mcp_server.py --transport streamable-http --host 127.0.0.1 --port 8010 --path /mcp
```

客户端连接 `http://127.0.0.1:8010/mcp`，并发送 `Authorization: Bearer <token>`。默认只监听回环地址。正式网络暴露前必须增加 HTTPS、企业 OAuth/网关、来源限制、速率限制和独立容量验收；不能直接把本地 8010 端口暴露到公网。

Streamable HTTP 不会回退使用 `QUERY_PILOT_MCP_TOKEN`；该变量只用于没有请求头的 stdio 传输。绑定非回环地址还必须显式增加 `--allow-remote`，防止误操作直接暴露本地服务。

## 5. Skill 路由语义

路由器使用确定性关键词和优先级，不调用模型，不执行数据库查询。响应包含候选 Skill、命中词、分数和方法版本；没有可靠命中时返回 `requires_clarification=true`，不会强行选择工作流。

业务准确性仍依赖指标口径、Schema、关系和真实企业评测集。关键词路由只是低成本第一层，不等于完整意图理解。

## 6. Skill 接入查询与发布前评测（SKILL-06）

- 查询图解析 Skill 使用者当前租户、主体和角色的 active/canary 版本。未命中时沿用原有查询上下文，不强制套用 Skill。
- 命中声明会作为结构化 `context.skill` 交给 SQL 生成器；最终 SQL 仍需通过现有只读 AST/字段权限/行级策略/审批/成本门禁和 Data Gateway。Skill 不获得执行工具，也不能跳过安全检查。
- 查询响应与 `resolve_semantics` 任务步骤记录 Skill ID、版本、发布状态、命中词和步骤，以便按任务追溯灰度命中。
- 管理员可以在新版本草稿中编辑触发词、意图和工作步骤，并在发布前调用固定集比较当前租户目录与候选定义。普通用户不能访问评测 API。
- 固定集位于 `data/skill_routing_eval.json`，当前 10 条问题覆盖销售、毛利、回款、趋势、Schema、SQL 诊断和未命中。输出分别统计路由选择变化、版本变化及对期望 Skill ID 的命中率。
- 也可在项目根目录使用 Windows CMD 运行：

```cmd
.venv\Scripts\python.exe scripts\evaluate_skill_routing.py --candidate data\skill-candidate.example.json --output artifacts\skill-routing-eval.json
```

该评测只验证确定性 Skill 路由，不衡量 SQL 语法/执行正确性、业务结果准确率或模型是否遵循工作步骤。当前 10 条用例是本地固定回归集，生产效果需补充企业用户问句、gold SQL/结果集，并在线观察采纳、修正和失败案例。

## 7. SQL 与查询结果质量评测（SKILL-07）

管理员可在 `/admin/skills` 草稿编辑区点击“评测 SQL 效果”，调用 `POST /api/v1/skills/admin/evaluate-query-quality`，对当前基线与候选 Skill 分别生成查询并对照固定数据集。该 API 与路由评测一样要求 `manage` 权限；未配置当前租户模型 API Key 时返回 `409`，不会把密钥或模型凭据写进报告。

- 数据集 `data/skill_query_eval.json` 当前为 `2026-11-04-v3`，含 11 条 gold SQL、预期列和预期行；在原有聚合、排序、关联、LEFT JOIN、日期和状态案例上，增加订单明细与分笔收款两个事实侧先按订单预聚合再 JOIN 的 CTE 案例。
- 每例各生成基线和候选一次，因此当前为 22 次生成尝试；`generation_attempts` 表示调用生成器的尝试数，不承诺真实 provider HTTP 请求数（缓存可能命中）。单次评测上限为 20 条用例。报告记录模型名、Prompt 版本和数据集版本。
- gold SQL 先在临时合成 SQLite fixture 中校验；生成 SQL 必须通过既有 `validate_sql`。只有通过校验的语句才会在 `mode=ro`、`query_only` 开启、5 秒查询 deadline 和 100 行上限下执行。评测不连接已配置业务数据源。
- 对比报告分别统计生成成功率、SQL 合法率、护栏通过率、执行成功率和精确结果匹配率，并按用例显示基线/候选状态。报告不保存生成 SQL 或完整结果行。
- 修改草稿会清除旧评测结果，避免把旧版本分数误认作当前候选版本；模型未配置和 API 错误会显示为页面错误状态。

本数据集是合成 SQLite 的小型回归样本，精确匹配只对当前固定用例成立，不能证明真实业务口径正确、不能替代企业 gold 集，也不能直接推导生产准确率。Skill 工作步骤仍是模型上下文声明，不是确定性可执行工作流。

合成案例现已覆盖两个独立事实侧同时 JOIN 前的预聚合，并允许受限的非递归 CTE 通过共享 SQL 护栏；递归 CTE、超过 8 个 CTE、重复/覆盖业务表名称和不明确输出列仍会被拒绝。该案例只证明固定数据下能够识别并回归一种扇出模式，不能据此宣称复杂企业多侧 JOIN、业务粒度或真实指标口径均已验证。

## 8. 分阶段改造计划

### 第一批：受控 MCP 与内置 Skill（已完成）

- [x] `SKILL-01` 声明式 Skill 契约与版本
- [x] `SKILL-02` 确定性路由和匹配证据
- [x] `SKILL-03` 五个内置只读分析 Skill
- [x] `SKILL-04` 认证 Skill API
- [x] `MCP-01` 官方 Python MCP 2.x Server 与五个 Tools
- [x] `MCP-02` Bearer/API Key 身份映射、租户 Resource 和协议回归

### 第二批：外部 MCP 与 Skill 发布治理（已完成）

- [x] `MCP-03` 外部 MCP Server 注册表、启停和允许工具白名单
- [x] `MCP-04` 连接健康、超时、熔断、并发和调用预算
- [x] `MCP-05` 管理端配置页面（后端密钥引用已由 MCP-03 完成）
- [x] `SKILL-05` 管理员版本发布、灰度启用和回滚
- [x] `SKILL-06` 声明编辑、查询运行时上下文接入与固定路由评测
- [x] `SKILL-07` 合成 SQL/结果集对照评测、管理员页面和安全护栏验证
- [x] `SKILL-08` 扩充 10 条分层合成评测，并修复布尔条件被误判为函数的问题
- [x] `SKILL-09` 非递归 CTE 安全边界与双事实表预聚合评测

当前整体 MCP/Skill 专项进度：`14/14（100% 本地实现）`。这表示本专项本地开发与回归清单完成，不表示企业生产验收或 SQL/业务准确率达标。企业生产仍需独立验证多实例并发发布、身份审批、集中审计、灾备、出口网络、证书、Redis 高可用、容量阈值和第三方 Server 的实际行为。
