# 对话上下文压缩

Query Pilot 的任务历史与模型 Prompt 使用不同的数据视图：原始 `TaskStep` 始终是审计和回放的权威记录；模型上下文是在每次追问时，从原始步骤重新生成的有界投影。压缩不会删除任务步骤，也不会改变 `/api/v1/tasks/{task_id}/conversation` 返回的完整会话。

## 上下文结构

当前版本为 `conversation-context-v2`，由三层组成：

1. `turns`：最近若干个当前仍有权限访问的成功查询轮次。
2. `summary.facts`：旧轮次的确定性 SQL lineage 事实。
3. `retrieved_turns`：根据当前问题与历史问题、表名和字段名进行词法相关召回的旧轮次。

每个事实都保留原始步骤引用，例如 `steps:21-22` 和 `source_steps: [21, 22]`，便于从模型投影回到任务审计记录。

## 保留与排除

压缩事实由 SQLGlot 解析已成功执行的 SQL，保留：

- 表和字段 lineage；
- 结果列名和返回行数；
- `GROUP BY`、`ORDER BY`、`LIMIT`、`DISTINCT` 等结构标记；
- 原始 TaskStep 引用。

模型上下文不包含：

- 结果行；
- SQL 参数和值；
- 原始 SQL 文本；
- 模型生成的 explanation 或 summary；
- 失败查询的 SQL 事实。

## 权限与安全

历史轮次不会沿用旧权限。每次追问都使用当前调用者可见的 Schema 重新校验历史表和字段；已撤权或当前不可见的轮次会被完整排除。生成上下文后，仍会进入现有不可信上下文清洗流程，历史问题不能绕过 Prompt 注入防护。

当前校验对复杂别名和 CTE 采取安全优先策略：无法确认字段仍被授权时会丢弃该历史轮次，而不是放宽权限。这样可能降低召回率，但不会为了保留历史而扩大当前权限。

## 预算配置

```dotenv
CONVERSATION_COMPACTION_THRESHOLD_TURNS=8
CONVERSATION_RECENT_TURNS=4
CONVERSATION_RETRIEVAL_TURNS=2
CONVERSATION_CONTEXT_MAX_CHARS=12000
```

裁剪顺序为旧事实、相关召回、较旧 recent turn、问题文本缩短，最后在极端宽表场景下允许移除最后一轮，以保证字符硬预算。任务步骤和 UI 会话历史不受该裁剪影响。

字符数只是 Token 成本的保守近似，并非 DeepSeek/OpenAI tokenizer 的精确 Token 计数。当前相关召回是词法与 SQL lineage 召回，不是向量检索；当前摘要也是确定性投影，不是 LLM 自动摘要。

## 可观测性

`understand_question` Step 和 `task_follow_up` Event 记录以下安全统计：

- 上下文版本；
- 原始、可见、过滤、压缩、最近和召回轮次数；
- 序列化字符数。

统计不保存问题正文、SQL、参数、结果值或模型原文。

## 已知边界

- 未使用供应商 tokenizer 做精确 Token 预算。
- 未使用向量历史检索或 LLM 自动摘要。
- 安全优先的别名、CTE 和复杂作用域判断可能过滤合法历史。
- 未完成企业超长会话、真实并发、多 Worker 和生产模型成本容量验收。
