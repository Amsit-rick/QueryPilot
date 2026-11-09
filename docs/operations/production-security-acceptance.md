# 本地生产安全验收与外部阻塞项

## 本地强制证据

CI 必须同时生成并通过以下报告：

- `reflection-release-gate.json`：动作、结果、安全三套反思评测的组合门禁。
- `sql-security-eval.json`：版本化 SQL 安全语料全部通过。
- `schema-retrieval-eval.json`：1500 表召回、关系路径、权限泄漏和 P95 门禁。
- `redis-recovery-drill.json`：独立 Redis 进程中断、checkpoint 恢复、单调 fencing 和旧持有者拒绝。
- `schema-embedding-redis-drill.json`：真实临时 Redis 下验证文档向量共享缓存、TTL、原始 Schema 不落缓存、共享熔断和故障前失败关闭；Embedding HTTP 响应仍为确定性合成数据。
- `multiprocess-metrics-acceptance.json`：隔离启动双 Worker Uvicorn，通过真实 HTTP 请求验证多进程指标文件和 `/metrics` 聚合，并检查临时进程及目录清理。
- `capacity-report.json`：固定请求数、并发数、成功率、P50/P95/P99 和错误分类。

汇总命令：

```cmd
.venv\Scripts\python.exe scripts\production_security_acceptance.py --reflection artifacts\reflection-release-gate.json --sql-security artifacts\sql-security-eval.json --schema artifacts\schema-retrieval-eval.json --redis artifacts\redis-recovery-drill.json --embedding-redis artifacts\schema-embedding-redis-drill.json --multiprocess-metrics artifacts\multiprocess-metrics-acceptance.json --capacity artifacts\capacity-report.json --output artifacts\production-security-acceptance.json
```

`local_gate_passed=true` 只表示这些本地证据完整，不等于 `production_ready=true`。

## 可观测配置

默认 `AGENT_TRACE_EXPORTER=jsonl`。需要集中追踪时安装 `requirements-observability.txt`，配置 `AGENT_TRACE_EXPORTER=otlp` 和 `AGENT_TRACE_OTLP_ENDPOINT`。OTLP 只导出 trace/task/tenant 标识、阶段、状态、耗时和稳定错误码，不导出 Prompt、SQL 参数、结果行或模型原始响应。Collector 不可用时本地 JSONL 仍保留，查询不会因遥测失败而失败。

Prometheus 同时输出反思延迟 histogram 的 bucket、count、sum，以及兼容的平均值和 P95 摘要。

## 尚需环境负责人验收

- 真实数据库只读账号、表/字段授权和 DBA 审计。
- 数据库与应用网络 IP 白名单、TLS 和证书轮换。
- Vault/KMS 密钥注入和应急轮换。
- OTLP Collector、Prometheus、Alertmanager/Grafana 的实际链路。
- Redis Sentinel/Cluster 切换，而非本地单实例重启。
- 外部 Embedding 供应商的真实语义效果、限流和故障行为；Redis 演练只覆盖本地共享治理。
- Prometheus 抓取、远端存储和 Alertmanager 通知；双 Worker 验收只覆盖应用侧聚合。
- 目标硬件、真实数据库和模型配置下的容量基线。

本轮按要求排除真实数据库验收，因此汇总报告会明确保留这些 blocker，不能据此宣称已经具备生产发布授权。
