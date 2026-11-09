# Query Pilot 运维操作

本目录记录本地和测试环境的可执行运维动作。它不是生产验收证明；正式环境必须替换容器名、凭据托管方式、备份目录和恢复审批流程。

## 启动与健康检查

确保 Docker Desktop 已运行，然后执行项目根目录的一键启动：

```cmd
scripts\start_local.cmd
```

检查服务和 API：

```cmd
scripts\check_stack.cmd
```

该脚本会检查 Compose 状态、API `/health`、API `/ready` 和前端入口；任一关键检查失败都会以退出码 1 结束，可直接作为部署门禁。脚本只读检查，不会停止或杀掉已有进程。检查结果只保留 HTTP 状态码和内容类型，不输出响应正文。

API 进程启动时也会执行配置门禁；生产模式下发现 JWT、CORS、Redis 后端或数据库凭据
不合规会直接拒绝启动。可先运行 `scripts\doctor.py` 查看完整错误列表，再启动服务。

`/health` 表示进程存活，`/ready` 还会检查数据库是否可用。若只看到 Redis 而没有 MySQL，先执行 `docker compose -f docker-compose.mysql.yml up -d`。

## MySQL 迁移预检

应用启动会自动执行未记录的 MySQL 迁移；发布前可先离线查看执行计划：

```cmd
.venv\Scripts\python.exe scripts\mysql_migrate.py --dry-run --report artifacts\migration-plan.json
```

预检只读取 `migrations/mysql`，不会连接数据库或修改数据，并会跳过 DBA 专用的 `V002`。
JSON 报告保存迁移文件名、大小、SHA-256 和语句数量，并明确记录
`database_connected=false`、`database_state_checked=false`。因此报告展示的是仓库内全部应用迁移文件，
不是目标库真实“待执行列表”。真实发布仍需连接目标数据库核对 `schema_migrations`、执行增量迁移，
并按回滚手册处理失败。

## MySQL 备份与恢复

备份和恢复脚本默认使用 `data_query_agent-mysql-1`、数据库 `query_agent`，密码从环境变量读取。Windows CMD 示例：

```cmd
set MYSQL_CONTAINER=data_query_agent-mysql-1
set MYSQL_ROOT_PASSWORD=你的实际root密码
scripts\backup_mysql.cmd backups
scripts\restore_mysql.cmd backups\query_agent_备份文件.sql
```

备份完成后可先执行只读校验，不会连接数据库，也不会执行恢复：

```cmd
scripts\verify_backup_mysql.cmd backups\query_agent_备份文件.sql
```

校验会检查文件存在且非空，并确认包含 MySQL dump 标识、`CREATE TABLE` 语句和
`schema_migrations` 结构。返回码为 0 才表示文件具备基本可恢复特征；这不是完整的
恢复演练，仍需在隔离环境验证导入和业务数据完整性。参数缺失或文件不存在返回 2，
文件为空或结构特征缺失返回 1。

通过结构校验后，可为备份生成并验证 SHA-256 清单：

```cmd
.venv\Scripts\python.exe scripts\operations\backup_manifest.py create backups\query_agent_备份文件.sql
.venv\Scripts\python.exe scripts\operations\backup_manifest.py verify backups\query_agent_备份文件.sql
```

清单应与 SQL 文件一起保存；校验失败表示文件大小或内容已变化。清单只能证明文件完整性，
不能替代隔离数据库中的实际导入和业务数据核对。

备份和恢复脚本不会再内置数据库密码；每次执行前都必须由调用环境设置
`MYSQL_ROOT_PASSWORD`，缺失时脚本会以返回码 2 退出。企业数据导入同样必须显式设置
`MYSQL_PASSWORD`，避免运维命令误连到错误账号或把开发密码带入生产。
备份脚本使用 UTC 时间戳生成文件名，数据库导出失败会删除半成品；密码通过
`MYSQL_PWD` 环境变量传递，不会出现在 `mysqldump` 或 `mysql` 的参数列表中。

恢复会覆盖目标数据库，脚本会二次确认。不要把密码写入脚本、文档或 Git；当前开发容器若使用不同 root 密码，必须显式设置 `MYSQL_ROOT_PASSWORD`。

## 故障处理顺序

1. `docker ps` 确认 MySQL、Redis 和 API 状态。
2. `scripts\check_stack.cmd` 检查 `/health` 与 `/ready`。
3. API 不可用时查看 `docker compose -f docker-compose.app.yml logs --tail 100 api`。
4. 数据库不可用时查看 `docker compose -f docker-compose.mysql.yml logs --tail 100 mysql`。
5. 仅在确认备份文件可读、目标环境和恢复窗口后执行恢复。

## 多实例限流

单进程开发默认使用内存滑动窗口。多实例或生产部署应设置：

```cmd
set RATE_LIMIT_BACKEND=redis
set REDIS_URL=redis://共享Redis地址:6379/0
```

Redis 模式使用原子脚本共享用户额度；Redis 故障时返回 503 并记录依赖不可用，避免
静默退回本地计数。部署前应在两实例环境验证同一用户的额度不会按实例数倍增。

模型熔断也可共享 Redis 状态：

```cmd
set LLM_CIRCUIT_BACKEND=redis
set REDIS_URL=redis://共享Redis地址:6379/0
```

共享熔断按模型服务地址和模型名隔离；达到失败阈值后所有实例都会拒绝新的模型调用，
Redis 故障时不会退回本地熔断计数。当前仍需在企业环境验证上游故障、恢复窗口和半开策略。

Schema Embedding 也可以共享文档向量缓存和熔断状态：

```cmd
set SCHEMA_EMBEDDING_CACHE_BACKEND=redis
set SCHEMA_EMBEDDING_CIRCUIT_BACKEND=redis
set REDIS_URL=redis://共享Redis地址:6379/0
```

Redis 缓存只保存命名空间哈希、文档哈希和向量，TTL 默认 24 小时，不保存原始 Schema 或用户问题。
配置为 Redis 后依赖不可用会让 Embedding 失败并降级到 BM25 + graph，不会静默改用每 Worker 本地缓存；
这是为了避免故障期间重复调用外部向量服务和不可控费用。正式环境仍需验证 Redis ACL、TLS、容量、淘汰策略和故障切换。

预算统计可设置为 Redis 共享日计数：

```cmd
set BUDGET_BACKEND=redis
set REDIS_URL=redis://共享Redis地址:6379/0
```

Redis 使用原子 `INCRBYFLOAT` 预留请求成本；超出单次或当日预算会返回
`E_BUDGET_EXCEEDED`，Redis 故障返回 `E_BUDGET_UNAVAILABLE`，不会退回每实例本地日志汇总。
查询入口已接入保守的模型成本预估：未提供显式 SQL 的自然语言请求会先预留预算，按字符近似输入 Token，并加固定输出上限；显式 SQL 不产生模型预算。直接模型和 LangChain 响应返回 usage 时，系统会在 SQL 执行前按真实 Token 补充预算差额，并将真实输入/输出 Token 写入审计；供应商未返回 usage 或缓存命中时继续使用估算值。确定性模板和显式 SQL 的审计 Token 为 0。审计 `usage_source` 区分 `provider`、`estimate` 和 `none`；Prometheus `/metrics` 会暴露对应累计计数，正式环境仍需用供应商账单校准 Token 单价。

审计摘要会跳过损坏或非对象日志行，并在 `/api/v1/observability/summary` 的 `invalid_records` 中报告数量；
单行日志损坏不会导致观测接口整体失败。`/metrics` 不再扫描审计文件，而是在审计成功落盘时更新原生
Prometheus Counter/Histogram；落盘失败由 `query_pilot_audit_write_failures_total` 记录。
Schema Embedding 调用另外记录请求状态、耗时、文档数和文档向量缓存命中/未命中；不记录问题文本、
Schema 文档、服务响应或密钥。
查询和审批审计记录同时保存 `trace_id`、`task_id`、`tenant_id`、操作者和审批 ID；只保存 SQL
哈希，不保存原始 SQL 或参数，便于按任务和租户关联事件而不扩大敏感数据暴露面。
历史日志中的非法数值字段会归一化为 0，并在审计摘要的 `invalid_values` 中统计。
审计 JSONL 默认达到 10 MiB 后轮转，保留 5 个备份文件，可通过 `AUDIT_LOG_MAX_BYTES` 和
`AUDIT_LOG_BACKUP_COUNT` 调整；观测摘要会读取当前文件与轮转文件，Prometheus 指标只反映当前进程生命周期内写入的事件。
本地内存预算只累计 UTC 当日记录，缺少或损坏时间戳的历史记录会保守计入，避免因坏数据低估预算。
生产多实例应把日志交给集中采集系统，不要依赖本地文件轮转作为长期留存。

### 多 Worker Prometheus

单进程默认使用进程内 Registry。使用 Uvicorn/Gunicorn 多 Worker 时，必须在启动任何 Python 进程前创建并
清空独立目录，然后设置 Prometheus 官方多进程环境变量；不要让两个不同部署共享同一目录。Windows CMD 示例：

```cmd
cd /d D:\QueryPilot
if exist .prometheus-multiproc rmdir /s /q .prometheus-multiproc
mkdir .prometheus-multiproc
set PROMETHEUS_MULTIPROC_DIR=%CD%\.prometheus-multiproc
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --workers 4
```

`/metrics` 会从该目录聚合所有 Worker，Worker 退出时执行进程死亡标记。启动检查会拒绝不存在或不可写的目录。
本地单进程开发不要设置该变量。目录只存指标分片，不是审计或长期监控存储；滚动发布、容器副本和 Kubernetes
仍应采用每 Pod 独立目录与 Prometheus 按实例抓取，不能让多个 Pod 写同一共享卷。

容器部署默认启用 `AUDIT_STDOUT_ENABLED=true`，每条成功落盘的脱敏审计记录会以单行 JSON 同步到
标准输出，包含 `schema_version` 和 `log_type=query_pilot_audit`，便于日志采集器识别。标准输出异常
不会影响本地权威文件；该能力只是采集入口，仍需在目标环境配置日志平台、租户访问权限和留存策略。
非容器环境默认关闭，可通过环境变量显式开启。

可按租户或任务导出审计留档（默认最多 10,000 行，包含当前和轮转日志）：

```cmd
.venv\Scripts\python.exe scripts\operations\audit_export.py --tenant-id tenant-a --output artifacts\tenant-a-audit.jsonl
.venv\Scripts\python.exe scripts\operations\audit_export.py --task-id TASK_ID --event approval_approved --output artifacts\task-audit.jsonl
```

导出工具只读取审计文件，跳过损坏行，不会修改源日志；写入采用临时文件原子替换，避免失败时留下半成品。
必须提供租户或任务范围，避免误导出全量跨租户记录。命令默认同时生成
`输出文件.manifest.json`，记录行数、大小和 SHA-256；租户/任务标识只写入哈希，不在清单中重复明文。
仅在调用方另有完整性机制时才使用 `--no-manifest`。

## 发布证据汇总

发布前先生成 CI 报告和 SBOM，再生成统一证据索引：

```cmd
.venv\Scripts\python.exe scripts\ci_verify.py --skip-frontend --skip-e2e --require-audit --report artifacts\ci-report.json
.venv\Scripts\python.exe scripts\mysql_migrate.py --dry-run --report artifacts\migration-plan.json
.venv\Scripts\python.exe scripts\generate_sbom.py --output artifacts\sbom.cdx.json
.venv\Scripts\python.exe scripts\operations\license_inventory.py --sbom artifacts\sbom.cdx.json --output artifacts\license-inventory.json --require-complete
.venv\Scripts\python.exe scripts\operations\image_inventory.py --output artifacts\image-inventory.json
.venv\Scripts\python.exe scripts\schema_embedding_redis_drill.py --redis-server "D:\path\to\redis-server.exe" --output artifacts\schema-embedding-redis-drill.json
.venv\Scripts\python.exe scripts\multiprocess_metrics_acceptance.py --workers 2 --requests 24 --output artifacts\multiprocess-metrics-acceptance.json
.venv\Scripts\python.exe scripts\operations\release_evidence.py --ci-report artifacts\ci-report.json --sbom artifacts\sbom.cdx.json --evidence migration=artifacts\migration-plan.json --evidence licenses=artifacts\license-inventory.json --evidence images=artifacts\image-inventory.json --evidence alerts=artifacts\alert-rules-validation.json --evidence schema-embedding-redis=artifacts\schema-embedding-redis-drill.json --evidence multiprocess-metrics=artifacts\multiprocess-metrics-acceptance.json --output artifacts\release-evidence.json
```

索引记录每个证据文件的大小和 SHA-256，并检查 CI 阻塞项、未执行项、必需门禁是否齐全、SBOM 格式和
Git 已跟踪文件状态。只有静态检查的报告会因缺少依赖扫描、后端全量和高风险回归而被判定为不完整；
前端构建和 E2E 由独立 CI job 执行，不在该索引中伪造结果。
证据索引会解析迁移、许可证和镜像报告中的格式、计数及状态，而不是仅检查文件存在和哈希。
文件损坏、计数矛盾、CI 未执行项或脏工作区写入 `problems`，并将 `local_evidence_complete` 设为
`false`；内容有效但尚未完成的外部事项写入 `production_blockers`，并保持
`production_evidence_ready=false`。默认命令只在本地证据无效时返回 1，便于日常 CI 持续归档；正式发布使用：

```cmd
.venv\Scripts\python.exe scripts\operations\release_evidence.py --ci-report artifacts\ci-report.json --sbom artifacts\sbom.cdx.json --evidence migration=artifacts\migration-plan.json --evidence licenses=artifacts\license-inventory.json --evidence images=artifacts\image-inventory.json --evidence alerts=artifacts\alert-rules-validation.json --evidence schema-embedding-redis=artifacts\schema-embedding-redis-drill.json --evidence multiprocess-metrics=artifacts\multiprocess-metrics-acceptance.json --output artifacts\release-evidence.json --require-production-evidence
```

严格模式还会因生产阻塞项返回 1；文件格式或参数错误返回 2。即使
`production_evidence_ready=true`，也只说明提供给索引的证据通过，不代表实际部署成功。

许可证盘点会从 Python 包元数据和 npm lockfile 写入的许可证声明生成清单；`--require-complete` 只要求
每个组件存在声明。即使 `inventory_complete=true`，`approval_status` 仍为 `not_reviewed`，必须由企业
法务或开源治理负责人确认策略、例外和义务后才能视为许可证审查完成。

镜像清单读取 `Dockerfile` 和生产 Compose，记录镜像来源、引用和 SHA-256 digest 锁定状态。当前
MySQL/Redis 已固定，`python:3.12-slim` 仍未固定；使用 `--require-pinned` 时会在存在未锁定引用时
写出报告并返回 1，适合作为正式发布门禁。默认 CI 仅归档报告，不阻塞日常回归。清单固定声明
`scan_status=not_run`、`signature_status=not_verified` 和 `approval_status=not_reviewed`，不能替代拉取、
漏洞扫描、签名验证或发布审批。

## Prometheus 告警

抓取配置 `deploy\prometheus.yml` 已加载 `deploy\prometheus-alerts.yml`。发布前执行结构校验：

```cmd
.venv\Scripts\python.exe scripts\validate_alert_rules.py
```

如需归档机器可读结果，可执行：

```cmd
.venv\Scripts\python.exe scripts\validate_alert_rules.py --report artifacts\alert-rules-validation.json
```

报告会明确记录本地结构校验结果、规则数量、阈值仍需企业确认，以及 `promtool` 和通知链路尚未执行；
它不能替代目标 Prometheus 环境中的官方校验和告警演练。

当前规则覆盖目标不可达、错误率、P95 耗时、审计写入失败、模型成本突增和 Schema Embedding 失败率。所有规则都带有
`threshold_status=needs_enterprise_confirmation`，处置步骤见
`docs\operations\alert-runbook.md`。本地校验检查 YAML、指标名称、严重级别、持续时间和运行手册引用；
目标环境仍需使用 `promtool check rules` 或企业 Prometheus 流水线校验 PromQL，并确认阈值、值班人和通知路由。
生产启用 `METRICS_TOKEN` 时，还必须在 Prometheus 抓取任务配置 Bearer 凭据文件，并通过企业密钥系统挂载；
不要把指标令牌直接写入 `deploy\prometheus.yml` 或提交到 Git。

## 运维演练记录

`docs\operations\drill-input.example.json` 是输入结构示例，不是演练证据。完成真实操作后，应使用实际时间、
环境、检查结果和证据文件创建记录：

```cmd
.venv\Scripts\python.exe scripts\operations\drill_record.py create artifacts\drill-input.json --output artifacts\drill-record.json
.venv\Scripts\python.exe scripts\operations\drill_record.py verify artifacts\drill-record.json --evidence-dir artifacts
```

支持 MySQL 恢复、数据库/Redis 故障切换、迁移回滚和服务恢复。标记为 `passed` 时，所有检查都必须为
`passed` 且至少有一个非空证据文件；生成的记录保存证据大小和 SHA-256。通过态记录在校验时必须提供
`--evidence-dir`，缺少原始证据或后续篡改都会导致校验失败。
`blocked` 和 `failed` 也应如实记录，不能为了通过门禁改写为成功。

## 尚待企业确认

- RPO/RTO、备份保留期、最大停机时间和告警阈值。
- MySQL/Redis 高可用、异地备份、密钥托管和恢复审批人。
- 真实生产故障演练记录。当前仓库只完成脚本和操作文档，不标记为生产演练通过。
