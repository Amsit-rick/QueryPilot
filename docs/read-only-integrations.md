# 只读集成调用契约（INT-01）

更新时间：2026-09-21

Query Pilot 提供一组可嵌入调用、HTTP 调用和标准 MCP 2.x Server 共用的只读工具契约。MCP 适配器复用本服务，不复制 Schema、权限或 SQL 校验逻辑；启动、身份映射和 Skill Resource 见 [`mcp-skills.md`](mcp-skills.md)。

## 安全边界

- 所有 HTTP 调用必须经过现有身份、租户和角色权限。
- 数据源 Schema 会按当前角色重新过滤；结果资产按任务属主和租户重新校验。
- `query.plan` 只验证问题和候选单条 `SELECT`，不会执行 SQL。
- 不提供 `query.execute` 工具。执行必须回到既有 `/api/v1/query` 或受管理权限保护的 Data Gateway 查询接口，因此不会绕过审批、审计、行列权限或脱敏。
- 审计记录只保存集成工具调用和 SQL 哈希，不保存外部调用方提交的 SQL 明文。

## HTTP 入口

```http
GET  /api/v1/integrations/read-only/tools
POST /api/v1/integrations/read-only/call
```

调用信封：

```json
{
  "tool": "schema.list",
  "request_id": "optional-client-id",
  "arguments": {
    "datasource_id": "sqlite-demo"
  }
}
```

成功响应统一包含：

```json
{
  "version": 1,
  "trace_id": "...",
  "tool": "schema.list",
  "read_only": true,
  "result": {}
}
```

## 工具

### `schema.list`

参数：`datasource_id`，省略时使用默认数据源。

返回当前身份可见的表和字段：

```json
{
  "datasource_id": "sqlite-demo",
  "tables": {
    "sales": ["region"]
  }
}
```

### `query.plan`

必填参数：`question`、可选 `datasource_id`、`sql`、`require_approval`。

- 不提交 `sql`：返回 `validation=deferred_to_query_execution`，表示需要调用方把问题交给主查询流程。
- 提交 `sql`：只通过现有 Schema、角色策略和只读护栏校验，不访问数据库；成功返回 `validation=passed` 和应用策略后的 SQL。
- `execution_delegated=true` 始终表示本工具不执行查询。

### `task.status`

参数：`task_id`。

只返回当前用户拥有的任务状态、问题、数据源、更新时间和最近事件类型。跨用户或跨租户任务返回 `404`。

### `result.get`

参数：`task_id`。

只返回当前用户拥有任务的持久化结果资产和受限预览元数据，不重新执行 SQL，不返回数据库连接信息。

## 错误

| HTTP | code | 含义 |
|---:|---|---|
| 401 | 认证错误 | 缺少或无效身份 |
| 403 | `E_DATASOURCE_FORBIDDEN` | 无权访问数据源 |
| 404 | `E_INTEGRATION_TOOL_NOT_FOUND` / `E_TASK_NOT_FOUND` | 工具或任务不存在 |
| 422 | `E_READONLY_VIOLATION` 等 | 参数、Schema 或 SQL 护栏失败 |

详细字段以运行中的 `/openapi.json` 为准。

## 嵌入式调用

受控 Python 集成可以直接复用同一服务，不必伪造 HTTP 身份：

```python
from app.services.read_only_integration_service import get_read_only_integration_service

service = get_read_only_integration_service()
result = service.invoke(
    {"subject": "internal-client", "tenant_id": "default", "roles": ["analyst"]},
    "schema.list",
    {"datasource_id": "sqlite-demo"},
)
```

嵌入式调用方必须由宿主应用先完成身份和租户映射，不能把外部用户输入直接伪造成管理员身份。

## 标准 MCP 入口

`scripts/run_mcp_server.py` 使用官方 Python `mcp 2.x` SDK 提供 stdio 和 Streamable HTTP。MCP 额外提供 `skill.route` 和按租户授权的 `querypilot://skills/catalog/{tenant_id}` Resource Template，但不提供 `query.execute`。

stdio 使用 `QUERY_PILOT_MCP_TOKEN` 映射现有 JWT 或 API Key；Streamable HTTP 必须在每个请求中携带 Bearer，不会回退使用进程级 stdio Token。非回环监听需要显式 `--allow-remote`，且不代表已完成企业 HTTPS/OAuth 网关验收。
