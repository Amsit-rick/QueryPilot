# Query Pilot 告警处置手册

本文件对应 `deploy/prometheus-alerts.yml`。其中阈值是保守的初始模板，所有规则均标记
`threshold_status=needs_enterprise_confirmation`；业务负责人和运维负责人确认 SLO、流量基线、
模型预算与值班制度前，不得将模板阈值描述为生产验收标准。

## QueryPilotTargetDown

确认 Prometheus 到 API 的网络、`/metrics` 鉴权和 API 进程状态，再检查 `/health`、`/ready`、
数据库及 Redis。不要因探针失败直接重启或杀掉未知进程；先确认当前部署方式和端口所有者。

## QueryPilotHighErrorRatio

按 `trace_id`、`task_id` 和错误码查看审计事件，区分模型、SQL 护栏、数据库、Redis 和权限失败。
若错误集中于单租户或单数据源，先隔离影响范围，不要全局放宽 SQL 安全策略。

## QueryPilotHighAverageLatency

检查数据库慢查询、连接池、模型响应时间、结果集大小和导出队列。当前规则使用原生 Prometheus
request-duration histogram 的 P95；阈值仍需结合目标环境流量和 SLO 确认。

## QueryPilotAuditWriteFailure

检查磁盘空间、目录权限、文件句柄和采集器占用。审计落盘失败属于阻断性问题；保留当前及轮转日志，
使用范围导出与 SHA-256 sidecar 保全已有记录，不手工修改原始审计文件。

## QueryPilotModelCostSpike

核对 `usage_source`、供应商 usage 和预算预留记录，区分真实 usage 与估算。必要时收紧租户预算或
暂停模型生成入口，但保留显式只读 SQL 和审批边界；模板中的每小时 10 美元必须按正式预算调整。

## QueryPilotLargeResultSpike

按租户、数据源和任务检查大结果查询，确认是否缺少业务筛选条件或需要转为异步导出。当前阈值按返回行数判断，
不代表数据库实际扫描行数；正式环境应结合数据库慢查询、`EXPLAIN` 成本和业务基线调整阈值。

## QueryPilotFrequentTruncation

检查调用方是否反复请求超过行数上限的数据，以及是否应使用分页、聚合或导出。不要通过取消最大行数限制消除告警；
先确认查询目的，并保留 AST 校验、只读账号和结果集上限。

## QueryPilotReflectionSafetyRollback

立即确认被回滚租户、版本 ID、原因码和当前 active 版本。安全/权限绕过必须保持零容忍；不要通过提高阈值恢复候选版本。先切回 `shadow`，复现并把脱敏 badcase 加入结果级与安全评测，再重新发布新版本。

## QueryPilotReflectionRepairFailureSpike

按失败类型区分 SQL 再生成、Schema 扩召回和执行重试，核对是否集中于特定 Schema、数据源或 Prompt 版本。失败率阈值是初始模板；在没有人工抽样确认前，不得把失败简单归因于模型。

## QueryPilotCheckpointRecoveryFailures

检查 Redis 可用性、租约 holder、fencing token 和 checkpoint schema version。旧 token 被拒绝属于安全保护，不应强制覆盖；如果结果资产不确定，任务必须收敛失败，禁止自动重跑 SQL。

## QueryPilotReflectionLatencyHigh

检查 Critic 调用、Schema 召回、EXPLAIN、数据库执行和 trace exporter。当前 P95 来自原生 Prometheus
Agent stage histogram；JSONL 只用于回放，不在抓取路径扫描。正式环境仍需验证多进程聚合、集中 tracing 和阈值。

## QueryPilotCriticFailures

区分超时和非法结构化响应。系统会保守转澄清，不应放宽允许动作集合或绕过结果规则。核对模型供应商健康、超时配置和出入参契约，不记录或复制模型原始响应到告警。

## QueryPilotBudgetExhaustion

区分模型费用预算和反思步骤预算。持续耗尽通常意味着问题歧义、Schema 召回不足或错误重试策略；不要单纯提高最大重试次数，应先分析原因码与 badcase。

## QueryPilotSchemaEmbeddingFailures

先查看 `/health` 中 `schema_embedding` 的稳定状态和错误码，再检查服务地址、模型标识、环境变量密钥引用、
超时和上游限流。失败时系统会降级到 BM25 + 关系图，不应为消除告警而跳过权限过滤或把未授权 Schema
发送给外部服务。若故障只影响远程服务，可临时切回 `SCHEMA_EMBEDDING_PROVIDER=disabled`，并保留召回评测证据。

## 关闭告警前

记录告警开始/恢复时间、影响租户、根因、处置和证据路径。涉及恢复、回滚或故障切换时，使用
`scripts\operations\drill_record.py` 形成机器可读记录；没有真实证据不得标为通过。
