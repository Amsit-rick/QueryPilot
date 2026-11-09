# Query Pilot 接口文档与测试规范

版本：2026-09-21
适用对象：接口测试人员、Postman/Apifox 使用者、自动化脚本开发者

本文定义 Query Pilot 本地测试环境的 HTTP 接口、权限、请求字段、响应结构和测试顺序。接口运行时的 OpenAPI 定义仍以以下地址为最终准：

```text
Swagger UI  http://127.0.0.1:8000/docs
OpenAPI JSON http://127.0.0.1:8000/openapi.json
```

## 1. 环境和约定

### 1.1 服务地址

| 名称 | 地址 |
|---|---|
| API 根地址 | `http://127.0.0.1:8000` |
| 前端 | `http://127.0.0.1:5173` |
| Swagger | `http://127.0.0.1:8000/docs` |
| OpenAPI | `http://127.0.0.1:8000/openapi.json` |

健康检查不需要登录：

```http
GET /health
GET /ready
```

成功时 HTTP 状态码为 `200`。`/ready` 还会检查数据库和应用依赖是否就绪。

### 1.2 首次启动

Windows CMD：

```cmd
cd /d D:\QueryPilot\QueryPilot-second-45e5df8
scripts\setup_local.cmd
scripts\check_stack.cmd
```

以后启动：

```cmd
cd /d D:\QueryPilot\QueryPilot-second-45e5df8
scripts\start_local.cmd
scripts\check_stack.cmd
```

不要同时运行 `docker-compose.app.yml`，也不要分别手动启动 API、前端和 MySQL。

### 1.3 Postman 环境变量

建议创建以下变量：

| 变量 | 示例 | 说明 |
|---|---|---|
| `base_url` | `http://127.0.0.1:8000` | API 根地址 |
| `username` | `admin` | 本地管理员账号 |
| `password` | `.env` 中 `LOCAL_ADMIN_PASSWORD` 的值 | 本地管理员密码，首次安装随机生成 |
| `access_token` | 登录响应中的值 | Bearer Token |
| `refresh_token` | 登录响应中的值 | 刷新会话 |
| `datasource_id` | `sqlite-demo` 或 `mysql-main` | 数据源 ID，以接口返回为准 |
| `task_id` | 查询响应中的值 | 任务 ID |
| `approval_id` | 审批查询响应中的值 | 审批 ID |
| `job_id` | 导出响应中的值 | 导出任务 ID |

JSON 请求统一使用：

```http
Content-Type: application/json
```

除登录、刷新、退出、健康检查外，Postman Collection 建议统一使用：

```text
Authorization: Bearer {{access_token}}
```

### 1.4 认证模式

`.env` 中的 `AUTH_ENABLED` 决定认证行为：

| 配置 | 行为 | 适用场景 |
|---|---|---|
| `false` | 使用本地开发身份 `local-dev`，角色为 `analyst` | 快速测试查询和导出 |
| `true` | 必须登录并携带 JWT；按用户、租户和角色执行权限检查 | 测试登录、权限、审批和管理员接口 |

交付包首次安装会生成 `AUTH_ENABLED=true`，然后由 `scripts\setup_local.cmd` 创建本地管理员 `admin`，密码为 `.env` 中 `LOCAL_ADMIN_PASSWORD` 的随机值。登录后即可测试完整认证链路、权限、审批和管理员接口；不要直接运行 Uvicorn。该账号仅用于隔离测试环境。

认证开启后，如果请求不带 Bearer Token，而是依赖 Cookie 发送写操作，还必须带：

```http
X-CSRF-Token: {{csrf_token}}
```

使用 Postman 的 Bearer Token 时，通常不需要额外发送 CSRF 头。

## 2. 权限模型

| 角色 | `query` | `export` | `manage` |
|---|---:|---:|---:|
| `viewer` | 是 | 否 | 否 |
| `analyst` | 是 | 是 | 否 |
| `admin` | 是 | 是 | 是 |

除角色权限外，所有数据源、任务、审批单和导出任务还受租户及属主范围限制。知道一个 `task_id`、`approval_id` 或 `job_id` 不能绕过授权。

## 3. 快速验收流程

1. `GET /health`
2. `GET /ready`
3. `POST /api/v1/auth/login`，保存 `access_token` 和 `refresh_token`
4. `GET /api/v1/auth/me`
5. `GET /api/v1/data-sources`
6. `GET /api/v1/data-gateways`
7. `POST /api/v1/query` 执行一条显式只读 SQL，并用 `POST /api/v1/query/stream` 验证相同请求的 NDJSON 结果流
8. `GET /api/v1/tasks/{task_id}` 检查状态、步骤和结果资产
9. `POST /api/v1/export`，轮询导出状态并下载 CSV
10. 使用无 Token、viewer 角色和非法 SQL 重复上述请求，检查授权和错误处理

## 4. 接口目录

| 模块 | 方法 | 路径 | 最低权限 | 用途 |
|---|---|---|---|---|
| 健康 | GET | `/health` | 无 | 存活检查 |
| 健康 | GET | `/ready` | 无 | 就绪检查 |
| 认证 | POST | `/api/v1/auth/login` | 无 | 登录 |
| 认证 | POST | `/api/v1/auth/refresh` | Refresh Token | 刷新会话 |
| 认证 | POST | `/api/v1/auth/logout` | 无 | 注销会话 |
| 认证 | GET | `/api/v1/auth/me` | 登录 | 当前用户 |
| 查询 | POST | `/api/v1/query` | query | 自然语言或显式 SQL 查询 |
| 查询 | POST | `/api/v1/query/stream` | query | 版本化 NDJSON 查询结果流 |
| 任务 | GET | `/api/v1/tasks` | 登录 | 当前用户任务列表 |
| 任务 | GET | `/api/v1/tasks/{task_id}` | 登录 | 任务详情和结果资产 |
| 对话 | GET | `/api/v1/tasks/{task_id}/conversation` | 任务属主 | 按轮次读取可恢复的多轮对话 |
| 反馈 | POST | `/api/v1/tasks/{task_id}/feedback` | 任务属主 | 记录采纳、修正、拒绝或失败案例 |
| 反馈 | GET | `/api/v1/tasks/{task_id}/feedback` | 任务属主 | 读取反馈和脱敏离线案例 |
| 数据源 | GET | `/api/v1/data-sources` | 登录 | 当前身份可用数据源 |
| Gateway | GET | `/api/v1/data-gateways` | query | Gateway 及能力矩阵 |
| Gateway | GET | `/api/v1/data-gateways/{id}/schema` | query | Gateway Schema |
| Gateway | GET | `/api/v1/data-gateways/{id}/preview` | query | 结果预览 |
| 审批 | GET | `/api/v1/approvals` | manage | 待审批列表 |
| 审批 | POST | `/api/v1/approvals/{approval_id}/approve` | manage | 通过并执行 |
| 审批 | POST | `/api/v1/approvals/{approval_id}/reject` | manage | 拒绝 |
| 导出 | POST | `/api/v1/export` | export | 创建 CSV 导出任务 |
| 导出 | GET | `/api/v1/export/{job_id}` | 登录 | 查询导出状态 |
| 导出 | GET | `/api/v1/export/{job_id}/download` | 登录 | 下载 CSV |
| 语义 | GET | `/api/v1/semantics/search` | 登录 | 检索语义条目 |
| 集成 | GET | `/api/v1/integrations/read-only/tools` | query | 查询只读工具清单 |
| 集成 | POST | `/api/v1/integrations/read-only/call` | query | 调用只读工具 |

## 5. 认证接口

### 5.1 登录

```http
POST {{base_url}}/api/v1/auth/login
Content-Type: application/json

{
  "username": "{{username}}",
  "password": "{{password}}"
}
```

成功响应 `200`：

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 900,
  "session_id": "..."
}
```

响应还会设置 HttpOnly Cookie `query_pilot_refresh` 和 CSRF Cookie `query_pilot_csrf`。

| 状态码 | 含义 |
|---:|---|
| `401` | 用户不存在、密码错误或账号未启用 |
| `423` | 失败次数过多，账号暂时锁定 |
| `422` | 请求字段不符合长度或格式要求 |

### 5.2 当前用户

```http
GET {{base_url}}/api/v1/auth/me
Authorization: Bearer {{access_token}}
```

成功响应包含 `subject`、`tenant_id`、`roles`、`username`、`role` 和 `is_active`。

### 5.3 刷新、退出和密码

刷新 Token：

```http
POST {{base_url}}/api/v1/auth/refresh
Content-Type: application/json

{ "refresh_token": "{{refresh_token}}" }
```

也可以只使用登录响应设置的 Refresh Cookie。刷新成功后旧 Refresh Token 会被撤销。

退出：

```http
POST {{base_url}}/api/v1/auth/logout
Content-Type: application/json

{ "refresh_token": "{{refresh_token}}" }
```

成功响应：`{"status":"ok"}`。修改密码：

```http
POST {{base_url}}/api/v1/auth/change-password
Authorization: Bearer {{access_token}}
Content-Type: application/json

{
  "current_password": "旧密码",
  "new_password": "至少 12 位的新密码"
}
```

## 6. 查询和任务接口

### 6.1 查询请求字段

```http
POST {{base_url}}/api/v1/query
Authorization: Bearer {{access_token}}
Content-Type: application/json
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `question` | string | 是 | 自然语言问题，1-2000 字符 |
| `sql` | string | 否 | 显式只读 SQL；不填时由模型或确定性模板生成 |
| `datasource` | string | 否 | 数据源 ID；不填使用默认数据源 |
| `params` | object | 否 | SQL 命名参数 |
| `max_rows` | integer | 否 | 1-10000，默认使用服务配置 |
| `offset` | integer | 否 | 分页偏移，默认 0 |
| `task_id` | string | 否 | 继续追问时填写原任务 ID |
| `follow_up` | boolean | 否 | 继续追问时为 `true` |
| `require_approval` | boolean | 否 | 是否先进入人工审批 |
| `analysis_mode` | string | 否 | `descriptive_statistics`、`trend`、`period_comparison`、`anomaly_detection` 或 `auto_insights`；省略时可由明确问题词触发 |

显式 SQL 示例：

```json
{
  "question": "查询华东销售额",
  "sql": "SELECT region, SUM(amount) AS total_amount FROM sales WHERE region = :region GROUP BY region",
  "datasource": "mysql-main",
  "params": {"region": "华东"},
  "max_rows": 100
}
```

自然语言生成 SQL 时，模型计划也可以返回同样的命名参数对象。参数值必须是字符串、整数、有限浮点数、布尔值或 `null`；嵌套对象、数组、非法参数名、超长字符串和超过配置上限的参数会被拒绝。若模型参数与请求中的同名参数值不同，接口返回 `E_QUERY_PARAM_CONFLICT`，不会静默覆盖调用方参数。

无显式 `sql`、需要调用模型的请求会先进行高置信度 Prompt 注入检测。检测到要求覆盖系统指令、泄露密钥/系统提示词、调用 Shell/网络/文件工具、绕过安全校验或生成写操作时，返回 `E_PROMPT_INJECTION`。这项检测是辅助防线；最终 SQL 仍必须通过 AST、Schema、权限和数据库只读账号校验。显式 SQL 不依赖文本启发式判断。

成功响应 `200`：

```json
{
  "trace_id": "...",
  "task_id": "...",
  "question": "查询华东销售额",
  "follow_up": false,
  "sql": "SELECT ...",
  "columns": ["region", "total_amount"],
  "rows": [["华东", 1960]],
  "row_count": 1,
  "truncated": false,
  "answer": "查询完成，共返回 1 行。",
  "analysis": null
}
```

请求智能分析时，`analysis` 返回结构化的 `metrics`、`trends`、`comparisons`、`anomalies`
和 `limitations`。趋势/比较需要至少两个可解析的时间点；异常检测至少需要四个时间点。
同一时间点出现多行时系统不会猜测聚合口径，而是在 `limitations` 中说明未生成该列的时序洞察。
百分比变化在基期为零时返回 `null`。若结果被截断，结论仅基于当前返回行并带限制说明。

查询护栏或执行失败也可能使用 HTTP `200`，但响应中会有 `error`：

```json
{
  "trace_id": "...",
  "task_id": "...",
  "error": {"code": "E_READONLY_VIOLATION", "message": "..."},
  "sql": "DELETE FROM sales"
}
```

测试脚本必须同时断言 HTTP 状态和 `error.code`，不能只判断 HTTP `200`。

### 6.2 流式查询

流式端点使用与同步接口相同的请求体和权限链路：

```http
POST {{base_url}}/api/v1/query/stream
Authorization: Bearer {{access_token}}
Content-Type: application/json
Accept: application/x-ndjson
```

响应为 `application/x-ndjson`，每行包含 `version`、`event` 和对象类型的 `data`。
当前协议版本为 `1`。成功序列为：

```text
started -> progress* -> result -> rows* -> completed
```

业务失败以 `error` 终止，进入审批以 `approval_required` 终止。`result.data` 只包含结果元数据，
不能包含 `rows`；行数据仅在 `rows.data.rows` 中按 `offset` 分块返回。

当前实现是数据库查询完成后的传输层分块，不是数据库游标级流式，也不支持断点续传。
客户端只在流端点返回 `404/405/406/415/501` 时回退同步接口；流已经开始后若版本未知、
JSON 损坏、单事件超过 5 MiB 或缺少终止事件，应失败关闭，不能自动重试查询。

完整事件字段、示例、取消语义和代理配置见 `docs/query-streaming.md`。

### 6.3 需要审批的查询

请求体只需增加：

```json
{
  "question": "导出销售明细",
  "sql": "SELECT region, amount FROM sales",
  "require_approval": true
}
```

成功进入审批状态时仍为 HTTP `200`：

```json
{
  "trace_id": "...",
  "task_id": "...",
  "status": "approval_required",
  "approval_id": "...",
  "sql": "SELECT region, amount FROM sales",
  "message": "高风险查询需要人工确认"
}
```

### 6.4 任务列表、详情和追问

```http
GET {{base_url}}/api/v1/tasks?limit=20
GET {{base_url}}/api/v1/tasks/{{task_id}}
GET {{base_url}}/api/v1/tasks/{{task_id}}/conversation
Authorization: Bearer {{access_token}}
```

任务详情返回任务字段以及 `steps`、`events`、`artifacts`。重点检查：

- 查询完成后任务状态为 `completed`。
- `artifacts` 至少包含表格结果；适合可视化时还包含图表配置。
- 页面刷新或重复调用详情接口后，结果资产仍存在。

继续追问：

```json
{
  "question": "只看华东区域",
  "task_id": "{{task_id}}",
  "follow_up": true,
  "datasource": "mysql-main"
}
```

只允许同一用户、同一租户、同一数据源的已完成任务追问。原结果资产不能被覆盖；跨用户、跨租户、跨数据源或未完成任务必须被拒绝。

对话接口把持久化任务步骤和表格结果资产投影为有序 `turns`，每轮包含 `question`、`follow_up`、
`status` 以及成功时的 `result`。前端刷新和从最近任务恢复时使用该接口，不依赖浏览器内存拼接历史。
它复用任务属主和租户校验，不提供其他用户任务的对话内容。

```json
{
  "task_id": "...",
  "datasource_id": "mysql-main",
  "status": "completed",
  "turns": [
    {"question": "查询销售总额", "follow_up": false, "status": "completed", "result": {"answer": "..."}},
    {"question": "按区域拆分", "follow_up": true, "status": "completed", "result": {"answer": "..."}}
  ]
}
```

## 7. 数据源和 Data Gateway 接口

### 7.1 数据源

```http
GET  {{base_url}}/api/v1/data-sources
GET  {{base_url}}/api/v1/data-sources/{{datasource_id}}/schema
POST {{base_url}}/api/v1/data-sources/{{datasource_id}}/test
POST {{base_url}}/api/v1/data-sources/{{datasource_id}}/refresh
Authorization: Bearer {{access_token}}
```

`/test` 和 `/refresh` 需要 `manage` 权限；列表和 Schema 只需要登录并且当前角色有权访问数据源。

### 7.2 Gateway 能力发现

```http
GET {{base_url}}/api/v1/data-gateways
Authorization: Bearer {{access_token}}
```

每个 Gateway 返回 `id`、`name`、`backend`、`dialect`、`readonly` 和 `capabilities`。能力字段为：

```json
{
  "connection_test": true,
  "schema": true,
  "preview": true,
  "query": true,
  "cancel": false,
  "health": true,
  "readonly": true
}
```

测试脚本应先检查能力，再调用操作，不应假设所有连接器都支持取消。

### 7.3 Schema、健康和预览

```http
GET {{base_url}}/api/v1/data-gateways/{{datasource_id}}/schema
GET {{base_url}}/api/v1/data-gateways/{{datasource_id}}/health
GET {{base_url}}/api/v1/data-gateways/{{datasource_id}}/preview?table=sales&limit=20&offset=0
POST {{base_url}}/api/v1/data-gateways/{{datasource_id}}/test
Authorization: Bearer {{access_token}}
```

预览参数：`table` 必填，`limit` 范围 1-100，`offset` 不小于 0。Schema 和预览会再次执行角色字段过滤，不能通过预览读取隐藏字段。

### 7.4 Gateway 查询和取消

Gateway 查询需要 `manage` 权限：

```http
POST {{base_url}}/api/v1/data-gateways/{{datasource_id}}/query
Authorization: Bearer {{access_token}}
Content-Type: application/json

{
  "sql": "SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region",
  "params": {},
  "max_rows": 100,
  "offset": 0
}
```

请求字段：`sql` 必填且最长 5000 字符；`params` 为对象；`max_rows` 至少 1；`offset` 不小于 0。只允许单条只读 SQL，并继续执行 Schema、字段和租户权限校验。

取消接口：

```http
POST {{base_url}}/api/v1/data-gateways/{{datasource_id}}/cancel
Authorization: Bearer {{access_token}}
Content-Type: application/json

{ "operation_id": "..." }
```

当前内置 Gateway 的 `cancel` 能力为 `false`，调用时预期返回 `409` 和 `E_GATEWAY_UNSUPPORTED`，这不是查询失败。

## 8. 审批接口

审批接口需要 `admin` 或其他拥有 `manage` 权限的角色：

```http
GET  {{base_url}}/api/v1/approvals
POST {{base_url}}/api/v1/approvals/{{approval_id}}/approve
POST {{base_url}}/api/v1/approvals/{{approval_id}}/reject
Authorization: Bearer {{access_token}}
```

审批通过返回 `approval_id`、`status=approved`、实际 SQL、列、行和行数；审批拒绝返回：

```json
{ "id": "...", "status": "rejected" }
```

审批通过后必须检查三个结果：

1. 审批状态为 `approved`。
2. 关联任务状态为 `completed`。
3. `GET /api/v1/tasks/{task_id}` 能读到表格和图表结果资产。

重复通过或重复拒绝同一审批单返回 `409`。跨租户审批单返回 `404`，无权访问其数据源返回 `403`。

自动质量门禁也使用同一审批接口。审批列表中的 `trigger=automatic` 表示由低置信度、模型风险标记、
通配符/大结果请求或候选异常触发，`reasons` 给出稳定原因代码。候选异常批准前不会创建结果资产。

### 8.1 任务反馈与离线案例

```http
POST {{base_url}}/api/v1/tasks/{{task_id}}/feedback
Authorization: Bearer {{access_token}}
Content-Type: application/json

{
  "outcome": "corrected",
  "note": "应按区域聚合",
  "corrected_sql": "SELECT region, SUM(amount) FROM sales GROUP BY region"
}
```

`outcome` 只允许 `accepted`、`corrected`、`rejected`、`failed`。修正 SQL 必须为当前属主有权访问的
单条只读查询；请求体不接受结果行或其他未知字段。密码、密钥、令牌和连接 URI 凭据会被拒绝。

```http
GET {{base_url}}/api/v1/tasks/{{task_id}}/feedback
Authorization: Bearer {{access_token}}
```

响应包含不可变反馈事件和 `evaluation_case` 资产。离线案例不含结果行和参数值，跨属主或跨租户访问
返回 `404`。

## 9. 导出接口

导出需要 `export` 权限。请求体沿用查询字段，但必须提供可执行的显式 `sql`：

```http
POST {{base_url}}/api/v1/export
Authorization: Bearer {{access_token}}
Content-Type: application/json

{
  "question": "导出区域销售额",
  "sql": "SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region",
  "datasource": "mysql-main",
  "params": {}
}
```

创建成功：

```json
{ "job_id": "...", "status": "queued" }
```

轮询和下载：

```http
GET {{base_url}}/api/v1/export/{{job_id}}
Authorization: Bearer {{access_token}}
```

当状态为 `completed` 后：

```http
GET {{base_url}}/api/v1/export/{{job_id}}/download
Authorization: Bearer {{access_token}}
```

下载响应为 `text/csv` 文件。任务不属于当前用户/租户、未完成或文件已清理时返回 `404`。

## 10. 语义接口

管理列表和版本历史需要管理员权限；普通查询用户只使用按当前 Schema 权限过滤的 `/search`：

```http
GET {{base_url}}/api/v1/semantics?datasource_id={{datasource_id}}
GET {{base_url}}/api/v1/semantics/search?datasource_id={{datasource_id}}&q=库存补货
GET {{base_url}}/api/v1/semantics/{{semantic_id}}/history
Authorization: Bearer {{access_token}}
```

创建语义条目需要管理员权限：

```http
POST {{base_url}}/api/v1/semantics
Authorization: Bearer {{access_token}}
Content-Type: application/json

{
  "datasource_id": "mysql-main",
  "kind": "sql_example",
  "name": "库存低于补货点",
  "description": "库存预警查询示例",
  "definition": {
    "question": "查询库存低于补货点的产品和仓库",
    "sql": "SELECT product_id, warehouse_id FROM inventory WHERE available_qty < reorder_point",
    "tables": ["inventory"],
    "columns": ["product_id", "warehouse_id", "available_qty", "reorder_point"],
    "aliases": ["库存预警"],
    "keywords": ["补货点", "低库存"]
  }
}
```

`kind` 支持 `metric`、`alias`、`value_dictionary`、`relationship`、`default_filter`、`sensitive_field` 和 `sql_example`。`sql_example` 只作为检索上下文，最终执行仍需重新经过只读和权限校验。

字段值词典示例：

```json
{
  "datasource_id": "sqlite-demo",
  "kind": "value_dictionary",
  "name": "销售区域值词典",
  "definition": {
    "target_table": "sales",
    "target_column": "region",
    "tables": ["sales"],
    "columns": ["region"],
    "values": [
      {"value": "EAST", "aliases": ["华东", "东区"]}
    ]
  }
}
```

值词典仅适合管理员维护的低基数字段，不要写入手机号、邮箱、证件号、密钥或自由文本。在线查询不会扫描业务表生成词典。

## 11. 模型和提示词运营接口

以下接口全部需要 `manage` 权限：

```text
GET  /api/v1/model-ops/providers
POST /api/v1/model-ops/providers
PATCH /api/v1/model-ops/providers/{provider_id}
POST /api/v1/model-ops/providers/{provider_id}/health
GET  /api/v1/model-ops/prompts?name=sql_generation
POST /api/v1/model-ops/prompts
POST /api/v1/model-ops/prompts/{prompt_id}/activate
GET  /api/v1/model-ops/runtime
```

创建模型提供商请求：

```json
{
  "name": "内部模型",
  "adapter": "direct",
  "base_url": "https://model.example.com/v1",
  "model": "model-name",
  "api_key_env": "QUERY_PILOT_MODEL_KEY",
  "enabled": true,
  "is_default": true
}
```

`api_key_env` 只填写环境变量名；请求体禁止提交 `api_key` 明文。响应只返回 `credential_configured`，不返回密钥。

创建提示词请求：

```json
{
  "name": "sql_generation",
  "system_prompt": "只生成单条只读 SELECT，并遵守当前 Schema 和权限。",
  "activate": true
}
```

## 12. 只读集成工具

工具清单：

```http
GET {{base_url}}/api/v1/integrations/read-only/tools
Authorization: Bearer {{access_token}}
```

统一调用：

```http
POST {{base_url}}/api/v1/integrations/read-only/call
Authorization: Bearer {{access_token}}
Content-Type: application/json

{
  "tool": "query.plan",
  "request_id": "postman-001",
  "arguments": {
    "question": "查询各区域销售额",
    "datasource_id": "sqlite-demo",
    "sql": "SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region"
  }
}
```

当前工具只有：`schema.list`、`query.plan`、`task.status`、`result.get`。成功响应固定包含 `version`、`trace_id`、`tool`、`read_only=true` 和 `result`。`query.plan` 只校验 SQL，不执行查询；当前没有 `query.execute` 工具。字段级说明见 [read-only-integrations.md](read-only-integrations.md)。

## 13. 管理员和会话接口目录

管理员接口均需要 `manage` 权限；请求字段和枚举以 `/openapi.json` 为准：

```text
GET    /api/v1/admin/users
POST   /api/v1/admin/users
PATCH  /api/v1/admin/users/{user_id}/status
PATCH  /api/v1/admin/users/{user_id}/role
POST   /api/v1/admin/users/{user_id}/reset-password
DELETE /api/v1/admin/users/{user_id}
GET    /api/v1/admin/users/{user_id}/grants
POST   /api/v1/admin/users/{user_id}/grants
DELETE /api/v1/admin/grants/{grant_id}
GET    /api/v1/admin/role-policies
PUT    /api/v1/admin/role-policies
GET    /api/v1/admin/config
```

普通用户会话和最近查询：

```text
GET    /api/v1/auth/sessions
POST   /api/v1/auth/sessions/{sid}/revoke
POST   /api/v1/auth/sessions/revoke-all
GET    /api/v1/sessions
DELETE /api/v1/sessions/{session_id}
```

## 13.1 补充路由清单

以下清单补充前文未展开的路由；完整路由和方法仍以当前应用的 `/openapi.json` 为准。其中 `/metrics` 为内部指标接口，不纳入普通接口测试。

```text
GET    /
GET    /health
GET    /ready
GET    /api/v1/schema
GET    /api/v1/retrieval
GET    /api/v1/observability/summary
PATCH  /api/v1/auth/profile
GET    /api/v1/auth/sessions
POST   /api/v1/auth/sessions/{sid}/revoke
POST   /api/v1/auth/sessions/revoke-all
POST   /api/v1/auth/change-password
GET    /api/v1/admin/export-dead-letters
POST   /api/v1/admin/export-dead-letters/{job_id}/retry
GET    /api/v1/data-gateways/{id}/health
POST   /api/v1/data-gateways/{id}/test
POST   /api/v1/data-gateways/{id}/cancel
GET    /api/v1/model-ops/providers
POST   /api/v1/model-ops/providers
PATCH  /api/v1/model-ops/providers/{provider_id}
POST   /api/v1/model-ops/providers/{provider_id}/health
GET    /api/v1/model-ops/prompts
POST   /api/v1/model-ops/prompts
POST   /api/v1/model-ops/prompts/{prompt_id}/activate
GET    /api/v1/model-ops/runtime
POST   /api/v1/tasks
PATCH  /api/v1/tasks/{task_id}/status
GET    /api/v1/semantics
POST   /api/v1/semantics
PATCH  /api/v1/semantics/{id}
GET    /api/v1/semantics/{id}/history
```

这些接口仍遵守本文前面的认证和权限模型。管理员死信重试、手工任务状态修改、配置查看和模型运营属于运维或管理测试，不应与普通查询成功率混在同一个业务用例中。

## 14. 错误处理规范

### 14.1 通用 HTTP 状态码

| 状态码 | 含义 | 常见场景 |
|---:|---|---|
| `200` | 请求处理完成 | 查询成功；查询业务错误可能也以 200 返回 `error` |
| `401` | 身份无效 | 缺少或过期 Bearer/Refresh Token |
| `403` | 没有权限 | 角色、租户、数据源或字段权限不足 |
| `404` | 资源不存在或不可见 | 任务、审批、导出任务不存在；跨属主资源通常也返回 404 |
| `409` | 状态或能力冲突 | 重复审批、非法状态转换、不支持 cancel |
| `422` | 参数或安全校验失败 | 字段格式、SQL 写操作、未知字段、多语句 |
| `429` | 限流或预算限制 | 请求频率或模型预算超限 |
| `502` | 下游服务失败 | 数据库、Gateway 或连接检查异常 |
| `503` | 服务暂不可用 | 队列、依赖服务或就绪检查失败 |

### 14.2 结构化错误

部分接口返回：

```json
{
  "detail": {
    "code": "E_READONLY_VIOLATION",
    "message": "只允许单条只读 SELECT",
    "operation": "query",
    "retryable": false
  }
}
```

查询接口的业务错误通常是：

```json
{
  "trace_id": "...",
  "task_id": "...",
  "error": {
    "code": "E_HALLUCINATED_FIELD",
    "message": "查询引用了不存在的字段"
  }
}
```

测试断言应优先使用稳定的 `code`，不要依赖完整中文 `message`。常见业务码包括：

| code | 含义 |
|---|---|
| `E_READONLY_VIOLATION` | 写操作或多语句被拒绝 |
| `E_HALLUCINATED_FIELD` | 未知表或字段 |
| `E_TABLE_FORBIDDEN` | 当前角色不能访问数据表 |
| `E_COLUMN_FORBIDDEN` | 当前角色不能访问字段或使用 `SELECT *` |
| `E_GATEWAY_UNSUPPORTED` | Gateway backend 或操作不支持 |
| `E_GATEWAY_FORBIDDEN` | 无权访问 Gateway |
| `E_GATEWAY_LIMIT` | 行数、分页或其他 Gateway 参数超限 |
| `E_TASK_NOT_FOUND` | 任务不存在或不属于当前身份 |
| `E_INTEGRATION_TOOL_NOT_FOUND` | 只读工具不存在 |

## 15. 必测用例

| 编号 | 用例 | 预期 |
|---|---|---|
| T01 | `/health`、`/ready` | 均为 `200` |
| T02 | 正确账号登录 | `200`，得到 Token |
| T03 | 错误密码登录 | `401` |
| T04 | 显式 SELECT 查询 | `200`，有 `task_id`、列和行 |
| T05 | 查询 `DELETE/UPDATE/DROP` | `error.code=E_READONLY_VIOLATION` |
| T06 | 查询未知字段 | `error.code=E_HALLUCINATED_FIELD` |
| T07 | 查询完成后刷新任务详情 | 状态和结果资产仍存在 |
| T08 | 审批通过 | 审批 `approved`、任务 `completed`、结果可读 |
| T09 | 重复审批 | `409` |
| T10 | 导出、轮询、下载 | 最终得到 CSV |
| T11 | viewer 调用管理接口 | `403` |
| T12 | 其他用户读取已知 task_id | `404` |
| T13 | 只读 `query.plan` | 只校验，不执行 SQL |
| T14 | 非法图表配置 | 被拒绝，不能执行脚本或加载远程资源 |
| T15 | 流式查询成功 | `result` 无 `rows`，分块有序，最终收到 `completed` |
| T16 | 流协议未知版本或超大事件 | 客户端失败关闭，不回退同步接口重复执行 |

## 16. 缺陷提交格式

```text
接口：方法 + 路径
环境：AUTH_ENABLED、数据源 ID、客户端版本
请求时间：
请求头：隐藏 Authorization、Cookie 和 CSRF Token
请求体：隐藏密码、密钥和 Token
HTTP 状态码：
响应体：
预期结果：
实际结果：
trace_id / task_id / approval_id / job_id：
复现步骤：
scripts\check_stack.cmd 输出：
```

不要把 `access_token`、`refresh_token`、模型 API Key、数据库密码或完整 `.env` 放入截图、缺陷单、Postman 集合或日志附件。
