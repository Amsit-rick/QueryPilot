# Query Pilot 查询结果流协议

Query Pilot 保留 `POST /api/v1/query` 同步接口，并在
`POST /api/v1/query/stream` 上同时支持兼容协议 v1 和可恢复协议 v2。两个版本都复用
`QueryApplicationService`，认证、租户/RBAC、Prompt 注入检测、SQLGlot 校验、审批、
EXPLAIN 成本门禁、字段脱敏、任务持久化和审计不会形成第二套执行旁路。

## 1. 能力与边界

- 响应类型为 `application/x-ndjson`，每行一个完整 JSON 事件。
- v1 是兼容协议，只支持当前连接内的事件消费。
- v2 使用状态快照、单调序号和有界增量日志，连接中断后可使用同一
  `client_request_id` 和 `after_sequence` 恢复展示，不再次执行查询。
- 当前仍是“数据库完整执行后再分块发送结果”，不是数据库游标级流式，不能降低数据库执行阶段的内存占用。
- 生产者任务与 HTTP 客户端连接解耦。客户端断开不会主动取消已经开始的查询，避免任务、审计和结果资产停留在不一致状态。
- 状态受 TTL 和最大事件数约束。游标过旧时返回最新完整快照；状态整体过期后不能无限恢复。
- `memory` 后端仅适用于本地或单 Worker；`redis` 后端支持多 Worker 共享短期流状态。当前未完成 Redis Sentinel/Cluster、网络分区或多机故障切换生产验收。

## 2. 请求与版本协商

### 2.1 v2 请求

```http
POST /api/v1/query/stream?after_sequence=3
Authorization: Bearer {{access_token}}
Content-Type: application/json
Accept: application/x-ndjson
X-Query-Stream-Version: 2
```

首次请求不传 `after_sequence`。连接中断后，客户端使用最后一个已完整处理的事件序号重连。

```json
{
  "client_request_id": "0b94c065-75d4-4fe0-9bf9-7a3940dbf95c",
  "question": "按区域统计销售额",
  "datasource": "mysql-main",
  "max_rows": 100
}
```

v2 强制要求 `client_request_id`，缺失返回 HTTP `422` 和
`E_QUERY_STREAM_ID_REQUIRED`。流作用域为：

```text
tenant_id + owner_id + operation + client_request_id
```

同一作用域的重连复用同一个 `stream_id` 和服务端生产任务。`after_sequence` 超过服务端最新序号时返回 HTTP `409` 和 `E_QUERY_STREAM_CURSOR_AHEAD`。

成功响应包含：

```text
Content-Type: application/x-ndjson
Cache-Control: no-store
X-Content-Type-Options: nosniff
X-Accel-Buffering: no
X-Query-Stream-Version: 2
X-Query-Stream-Id: <uuid>
```

### 2.2 v1 兼容请求

不发送 `X-Query-Stream-Version: 2` 时继续使用 v1。v1 不要求恢复游标，合法序列保持：

```text
started -> progress* -> result -> rows* -> completed
started -> progress* -> error
started -> progress* -> approval_required
```

## 3. v2 事件信封

```json
{"version":"2","stream_id":"...","sequence":3,"event":"rows","data":{"offset":0,"rows":[["华东",1960]]}}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `version` | string | v2 固定为 `2` |
| `stream_id` | string | 服务端查询流标识；重连期间保持不变 |
| `sequence` | integer | 非负单调序号；delta 必须严格连续 |
| `event` | string | `snapshot` 或 v1 已有的业务事件 |
| `data` | object | 事件数据，不接受数组或标量 |

客户端必须拒绝未知版本、未知事件、无效 JSON、stream_id 变化、重复/倒序/跳号事件、单事件超过 5 MiB，以及超过恢复次数仍未终止的流。协议损坏不得触发同步接口重放。

## 4. Snapshot 与 Delta

### 4.1 `snapshot`

首次连接的首包是快照。事件日志已裁剪且客户端游标过旧时，也返回快照并使用
`reason=cursor_expired`。快照替换客户端本地投影，不做行追加。

```json
{
  "version": "2",
  "stream_id": "stream-uuid",
  "sequence": 5,
  "event": "snapshot",
  "data": {
    "reason": "cursor_expired",
    "state": {
      "status": "completed",
      "terminal": true,
      "task_id": "task-uuid",
      "result": {"columns":["region","amount"],"row_count":2},
      "rows": [["华东",60],["华南",40]],
      "progress": null,
      "completion": {"row_count":2,"chunk_count":2,"truncated":false},
      "error": null,
      "approval": null,
      "last_sequence": 5
    }
  }
}
```

### 4.2 Delta

快照之后继续使用已有事件：

- `started`：服务端生产任务已开始。
- `progress`：查询仍运行的心跳，不代表完成比例。
- `result`：结果元数据，不包含 `rows`。
- `rows`：经过字段权限与脱敏后的行分块。
- `completed`：成功终态。
- `error`：失败终态，只暴露稳定错误码和公共安全消息。
- `approval_required`：进入人工审批的终态。

每个 delta 的 `sequence` 必须等于前一已接受序号加一。客户端重连后只接受
`after_sequence` 之后的 delta；服务端不会因为重连新增 Task、Step、Event 或 Artifact。

## 5. 前端恢复策略

1. 首次请求生成并保存 `client_request_id`，发送 v2 请求头。
2. 每接收一个完整事件，验证 `stream_id` 与严格递增的 `sequence`，再更新本地投影。
3. 连接在合法 v2 事件后中断时，保留 `lastSequence`、结果元数据和已收行。
4. 使用相同 payload 和 `client_request_id`，携带 `after_sequence=<lastSequence>` 重连。
5. 自动恢复最多 2 次；协议错误、未知版本、超大事件或序号错误立即失败关闭。
6. 只有端点首次明确返回 `404`、`405`、`406`、`415` 或 `501` 时，才允许回退同步接口；恢复阶段不回退同步查询。

## 6. 状态后端与配置

```dotenv
QUERY_STREAM_CHUNK_ROWS=100
QUERY_STREAM_HEARTBEAT_SECONDS=5
QUERY_STREAM_STATE_BACKEND=memory
QUERY_STREAM_STATE_TTL_SECONDS=900
QUERY_STREAM_STATE_MAX_EVENTS=2048
QUERY_STREAM_POLL_SECONDS=0.05
REDIS_URL=redis://127.0.0.1:6379/0
```

- `memory`：默认值，状态只存在当前进程，进程退出或请求落到其他 Worker 后不可恢复。
- `redis`：scope key 使用身份作用域 SHA-256，不把问题、SQL、参数值写入 key；session、快照和有界事件列表使用相同 TTL。
- Redis 状态会临时保存用户已经有权查看的结果元数据和脱敏行，用于恢复展示；必须按租户/用户作用域访问，并设置短 TTL。它不是长期结果仓库。
- 配置为 Redis 但 Redis 不可用时失败关闭，不静默降级内存。

## 7. 验证范围

自动化测试覆盖：v1 兼容、v2 首包快照、严格序号、断线续传、重连不重复执行、客户端关闭后生产者继续、完整快照恢复、游标裁剪回退、超前游标拒绝、租户/用户隔离、协议损坏失败关闭、前端行不重复和 Redis 状态仓储。

本机真实 Redis 5.0 单实例验证了原子认领、重复请求复用、单调序号、事件裁剪与终态快照。该证据不等于 Redis HA、Cluster、跨机网络分区或无限历史恢复验收。
