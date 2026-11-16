# Query Pilot MCP 与 Skill 改造

更新时间：2026-11-16

## 1. 目标与边界

Query Pilot 将 MCP 用作跨客户端的结构化工具协议，将 Skill 用作按需加载的业务分析工作流。两者都不是新的安全边界：认证、租户、数据源权限、SQL AST 校验、审批、脱敏、Gateway 和审计仍由现有 Query Pilot 服务负责。

本阶段不提供裸 `query.execute` MCP Tool。`query.plan` 只校验计划和候选只读 SQL，不访问数据库；真正执行必须回到现有查询接口。

## 2. 当前架构

```text
MCP Client / Query Pilot HTTP Client
  -> Bearer JWT / API Key / 本地开发身份
  -> Skill Router（确定性关键词与优先级）
  -> 可选 Agent MCP Enrichment（固定只读语义工具）
  -> Schema 召回 / SQL 生成 / SQL 安全门禁
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
POST   /api/v1/mcp-servers/{server_id}/credentials/rotate
POST   /api/v1/mcp-servers/{server_id}/health
POST   /api/v1/mcp-servers/{server_id}/invoke
DELETE /api/v1/mcp-servers/{server_id}
```

注册表保存 `streamable-http` Endpoint、Secret 引用、认证方式、OAuth 非秘密元数据、精确 Tool allowlist 和运行时治理参数。
不接受 stdio 命令、内联 Token/Client Secret、任意 Header 或 `*` 通配符。远程 Endpoint 与 OAuth Token URL 必须使用 HTTPS；
HTTP 只允许 `localhost` 或字面量回环 IP。注册、健康检查、轮换和调用均要求 `manage` 权限、按租户过滤并写入脱敏审计事件。

示例请求只引用部署环境中的密钥，不包含密钥值：

```json
{
  "name": "Finance MCP",
  "transport": "streamable-http",
  "endpoint": "https://mcp.example.com/mcp",
  "auth_type": "bearer",
  "auth_env_ref": "env://FINANCE_MCP_TOKEN",
  "allowed_tools": ["schema.list", "report.read"],
  "enabled": true,
  "read_only_only": true
}
```

OAuth Client Credentials 示例：

```json
{
  "name": "Finance OAuth MCP",
  "transport": "streamable-http",
  "endpoint": "https://mcp.example.com/mcp",
  "auth_type": "oauth_client_credentials",
  "auth_env_ref": "env://FINANCE_MCP_CLIENT_SECRET",
  "oauth_token_url": "https://identity.example.com/oauth/token",
  "oauth_client_id": "query-pilot",
  "oauth_scopes": ["mcp.read"],
  "allowed_tools": ["report.read"],
  "enabled": true,
  "read_only_only": true
}
```

响应中的 `credential_configured` 只说明当前进程能否解析 Secret 引用，不返回 Secret。每次健康检查或调用都会重新解析目标 DNS、
发现远端工具并核验精确 allowlist。调用只放行明确声明 `readOnlyHint=true` 且未声明 `destructiveHint=true` 的工具；重复工具名、
缺少只读声明和交互式 `input_required` 均失败关闭。

### 外部调用治理

外部调用使用官方 Python `mcp==2.3.0` Streamable HTTP 客户端，并执行以下限制：

- Bearer Token 或 OAuth Client Secret 只从 `auth_env_ref` 指向的 Secret Provider 读取；当前 Provider 支持环境变量引用。
- OAuth Access Token 按租户、Server ID 和凭据版本缓存于进程内存，过期前刷新；不写入数据库、Redis、响应或审计。
- 凭据配置只能通过专用轮换接口修改；轮换增加版本、清除旧缓存并把健康状态重置为未检查。
- 禁用环境代理、禁止重定向，并限制参数、结果、SSE 单事件、工具数量和分页数量。
- 每个租户和 Server 独立执行并发、每日调用次数和熔断治理；健康检查使用独立单飞键；单机默认使用内存，多实例部署应使用 Redis。
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
MCP_AGENT_ENRICHMENT_ENABLED=false
MCP_AGENT_ENRICHMENT_MAX_TOOLS=2
MCP_AGENT_ENRICHMENT_MAX_RESULT_BYTES=32768
MCP_AGENT_ENRICHMENT_MAX_DEPTH=8
MCP_AGENT_ENRICHMENT_MAX_FIELDS=256
MCP_AGENT_ENRICHMENT_MAX_TEXT_CHARS=2000
MCP_AGENT_ENRICHMENT_MAX_QUESTION_CHARS=512
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

本数据集是合成 SQLite 的小型回归样本，精确匹配只对当前固定用例成立，不能证明真实业务口径正确、不能替代企业 gold 集，也不能直接推导生产准确率。除 `sales-analysis` 和 `trend-analysis` 已映射到平台固定执行模板外，其余 Skill 工作步骤仍只是模型上下文声明，不是可执行代码。

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

截至 2026-11-04 的 MCP Server、管理面与声明式 Skill 阶段进度为 `14/14（100% 本地实现）`。这只表示该历史阶段完成，不表示后续 Agent 主链路深化、企业生产验收或 SQL/业务准确率达标。企业生产仍需独立验证多实例并发发布、身份审批、集中审计、灾备、出口网络、证书、Redis 高可用、容量阈值和第三方 Server 的实际行为。

## 9. Agent MCP Enrichment、可执行 Skill 与企业治理（MS-00～MS-10）

旧 `14/14` 表示 2026-11-03 至 2026-11-04 的 MCP Server、管理面和声明式 Skill 阶段已经完成，
不表示外部 MCP 已经进入用户查询主链路。2026-11-16 新增深化专项后，当前专项进度为 `10/11（90.9%）`；
MS-10 已完成官方 SDK 协议基线，但产品客户端验收仍未完成。

查询链路现在支持在 Skill 路由后、Schema 召回前执行可选的外部 MCP 语义补充：

```text
用户问题
  -> 身份 / 租户 / 数据源权限
  -> Skill 路由
  -> 确定性 MCP 工具选择
  -> 外部结果大小 / 深度 / 字段 / 文本上限
  -> Prompt 注入清洗与不可信数据包装
  -> Schema 召回
  -> 平台固定 Skill 工作流预检
  -> SQL 生成
  -> 原有 AST / 权限 / 成本 / 审批 / 执行门禁
```

只允许四类工具进入该链路：`catalog.search`、`metric.lookup`、`lineage.get`、`policy.explain`。
普通数据查询不会调用 MCP；模型不能指定 Server、Endpoint 或任意工具。每类工具从当前租户已启用且显式允许该工具的
Server 中按稳定顺序选择一个。功能默认关闭，只有设置 `MCP_AGENT_ENRICHMENT_ENABLED=true` 才会启用。

发送给外部 MCP 的参数仅包含经过邮箱、手机号、长数字和常见 Secret 模式脱敏的问题摘要，以及数据源 ID 和数据库类型；
不发送 SQL、参数、Token、连接串或完整 Schema。返回结果必须是有界 JSON 对象，并以
`untrusted_external_data` 包装。检测到类似覆盖系统指令、索取 Secret 或绕过安全边界的文本时，用占位符替换并记录指纹数量。

MCP 超时、不可用、结果非法或超限时，查询链路记录 `degraded` 和稳定错误码后继续使用本地 Schema/语义能力；
不会把原始异常或结果注入模型，也不会跳过后续 SQL 安全链路。DataTask 的 `resolve_semantics` 步骤只保存 Server ID、
工具名、状态、耗时、错误码、结果 SHA-256 和安全发现数量，不保存完整请求或结果。

固定联合评测集 `data/mcp_skill_joint_eval.json` 当前 12 条，覆盖普通查询、指标、血缘、策略、目录、注入、非法结果和
上游超时。运行方式（Windows CMD）：

```cmd
.venv\Scripts\python.exe scripts\evaluate_mcp_skill_joint.py --output artifacts\mcp-skill-joint-eval.json
```

该联合集只验证 Skill/MCP 路由、安全清洗和降级契约，不连接真实远程 Server，不衡量 SQL 结果或业务口径准确率。

### Executable Skill Graph

首批仅将 `sales-analysis` 和 `trend-analysis` 映射为平台拥有的固定模板：

```text
clarify
  -> retrieve_metric
  -> retrieve_schema
  -> validate_grain
  -> generate_sql
  -> validate_sql
  -> execute
  -> verify_result
  -> explain
```

模板固定声明步骤、允许工具、单步超时、最大调用次数，以及外部工具、模型调用、Token 和费用预算。
模糊的“销售分析”等问题会在模型调用前进入 `clarification_required`；显式 SQL 会跳过 SQL 生成步骤；
模型调用前实际执行预算门禁，超限返回稳定、不可重试的错误码。执行证据以独立 `skill_workflow` DataTask 步骤持久化，
只包含合同版本、步骤状态、原因码和有界统计，不包含问题原文、Prompt、SQL、参数、MCP 原始结果、Endpoint 或凭据。

租户在发布页填写的 `workflow_steps` 仍是声明元数据，不能覆盖平台模板，也不会被当作 Python、Shell、网络安装或任意工具调用执行。
`schema-exploration`、`receivables-analysis`、`sql-diagnostics` 当前仍为声明式 Skill。SQLGlot AST、RBAC、行级策略、
成本、审批和 Data Gateway 仍是唯一执行边界；可执行 Skill 不获得直接数据库权限。

### Controlled SKILL.md Import

管理员发布页支持选择或粘贴 `SKILL.md`，先调用预览 API，再显式确认创建草稿。只解析严格 YAML Front Matter，
大小上限为 64KB；未知/重复字段、YAML Anchor/Alias/Tag/Merge Key、Prompt 注入语义和现有 Skill 策略字段变更均失败关闭。
Markdown 正文只供人工阅读，不进入 Skill 定义、审计或运行时，也不会执行。

来源字段记录 `local / github / registry`、URI、修订版本和发布者。远程 URI 只作为元数据，后端不会联网下载。
SHA-256 只验证收到的内容与管理员声明一致，不构成发布者身份认证。预览令牌使用 HMAC 绑定内容、来源、定义、租户、
管理员和当前 Skill 基线，10 分钟后过期；基线并发变化必须重新预览。

导入确认只调用现有 `create_version` 创建 `draft`，不会自动灰度或发布。后续评测、灰度、发布和回滚仍走原治理链。
示例格式见 `data/skill-import.example.md`，TDD 证据见 `docs/testing/controlled-skill-md-import.tdd.md`。

### OAuth and Secret Governance

外部 MCP 支持 `bearer` 与 `oauth_client_credentials` 两种认证方式。数据库只保存 Secret 引用、OAuth Token URL、Client ID、
scope、凭据版本和轮换时间；不保存 Token 或 Client Secret。OAuth Token 请求禁用环境代理与重定向，响应限制 64KB，
只接受字符串 Bearer Token 和正数有效期。

普通 Server PATCH 不能修改认证配置，必须调用专用轮换端点。轮换会增加 `credential_version`、清除当前进程旧 Token 缓存、
重置健康状态并写入不含引用值的审计事件。缓存键包含租户、Server 和凭据版本，防止跨租户或轮换后复用。

现有 `MCP_RUNTIME_BACKEND=redis` 继续共享并发、每日调用预算和熔断状态；健康探针使用独立、最大并发为 1 的治理键。
Access Token 不写入 Redis，多实例分别向 OAuth Provider 获取 Token。证据见 `docs/testing/mcp-oauth-secret-governance.tdd.md`。

### Client Compatibility Matrix

官方 Python MCP SDK `2.3.0` 已在本机实际验证 stdio 与回环 Streamable HTTP。两种传输均覆盖初始化、五个只读 Tool、
Resource Template 列表与读取、`skill.route` 调用、稳定错误码 `E_INTEGRATION_QUESTION`、认证错误码 `E_MCP_AUTH`，
以及客户端取消后的传输清理。复现命令：

```cmd
.venv\Scripts\python.exe scripts\verify_mcp_client_compatibility.py --transport all --output data\mcp-client-compatibility.json
```

机器可读矩阵为 `data/mcp-client-compatibility.json`，详细说明见 `docs/mcp-client-compatibility.md`。
Claude Code/Desktop、Cursor、Codex 未在固定版本下完成全链路能力检查，矩阵中保持 `not_tested`；不能把官方 SDK 协议通过写成这些产品客户端已经兼容。

专项当前为 `10/11（90.9%）`。官方 SDK 双传输验证属于 MS-10 的协议基线，不等于主流产品客户端验收完成。
该进度仍不代表完整 MCP/Agent Skills 生态、任意 Skill 代码执行、企业 OAuth/Vault/KMS、多机网络、
真实长任务服务端取消或企业生产验收。
