# 生产上线门禁

生产上线前必须完成：企业 OIDC/JWT/JWKS、DBA 创建的只读账号、Vault/KMS 密钥注入、正式 MySQL/Oracle 验收、HTTPS、受限 CORS、外部监控告警、备份恢复演练、固定依赖和镜像漏洞扫描。

当前代码已加入合并前供应链基线：前端直接依赖使用精确版本并与 lockfile 对齐，应用容器
以非 root 用户运行，Redis 有健康检查且 API/worker 等待 Redis 就绪。该基线降低配置漂移和
容器权限风险，但不替代企业镜像签名、SBOM、基础镜像 digest、镜像扫描和漏洞修复流程。
依赖漏洞门禁在工具或 registry 不可用时会明确记录未执行，不能据此宣称生产依赖已安全。
正式发布流水线必须使用 `scripts/ci_verify.py --require-audit`，将扫描工具缺失、registry
不可用或扫描中断视为发布阻塞；开发机可继续使用默认告警模式。
门禁 JSON 报告会区分 `executed`、`blocking`、`not_executed` 和 `blocking_failures`，发布系统
应以这些字段判断证据完整性。API/worker 的生产 Compose 已配置非 root、禁止提权、drop
capabilities、PID/内存边界和 Redis 就绪依赖；仍需在目标运行时验证这些限制实际生效。
CI 同时归档带包元数据许可证声明的 CycloneDX 1.5 SBOM 和许可证盘点；CI 会阻止许可证声明缺失，
但盘点始终保持 `approval_status=not_reviewed`。SBOM 和声明完整度都不等于法务批准，生产上线前仍需
通过镜像扫描、依赖漏洞扫描和企业许可证策略审查。
备份、恢复和企业数据导入脚本不会使用内置数据库密码，必须由凭据系统或调用环境显式注入；
开发用 `docker-compose.mysql.yml` 也只接受 `.env` 注入的 root、控制面和只读查询密码，全新安装由
本地脚本生成三套不同随机值。普通启动不会自动轮换旧数据卷，避免 Compose 环境值与数据库内部密码失配；
本地维护人员可使用 `scripts/rotate_local_mysql_credentials.py` 显式轮换，正式执行必须提供已校验的备份
清单，并在数据库或 `.env` 更新失败时执行补偿回滚。该本地工具不替代生产 Vault/KMS、DBA 审批和
集中轮换审计。
备份文件还应生成并保存 SHA-256 清单；清单校验通过只证明文件未被修改，仍必须在隔离数据库
执行恢复和业务数据抽样核对后，才能形成恢复演练证据。
MySQL dry-run 报告只枚举仓库迁移文件及其 SHA-256，不连接数据库或读取已应用版本；不得把报告中的
`planned_count` 当作目标库真实待迁移数量。

Prometheus 告警模板覆盖存活、错误率、平均耗时、审计损坏和模型成本；模板阈值均标记为需企业确认。
本地 YAML 校验和运行手册不能替代目标 Prometheus 的 `promtool` 校验、告警通知链路和值班演练。

CI 会生成容器镜像离线清单，覆盖 `Dockerfile` 基础镜像和生产 Compose 中的外部服务镜像。
当前清单如实显示 MySQL/Redis 已固定 digest、`python:3.12-slim` 未固定，并保持镜像扫描、签名验证和
审批状态为未执行。正式发布应先在可信 registry 确认 Python 基础镜像 digest，再以
`scripts\operations\image_inventory.py --require-pinned` 阻塞未锁定引用；随后仍需执行企业镜像扫描和签名验证。

多实例部署还必须将 `RATE_LIMIT_BACKEND` 设置为 `redis` 并配置共享 `REDIS_URL`。默认
`memory` 仅适用于单进程开发环境；Redis 不可用时共享限流会拒绝请求（503），不会降级
为每实例独立额度。

模型熔断同样应设置 `LLM_CIRCUIT_BACKEND=redis`，使故障计数和熔断窗口在实例间共享；
未完成双实例故障切换演练前，不得把本地模拟 Redis 测试当作生产可用性证据。

预算统计应设置 `BUDGET_BACKEND=redis`，通过共享日计数避免多实例重复使用本地审计额度。
正式上线前还必须确定 token/成本估算来源，并在真实模型调用中验证超限和跨日过期。

本地容器验证（Windows cmd）：

```cmd
cd /d D:\QueryPilot
set MYSQL_PASSWORD=由凭据系统注入的query_app密码
set MYSQL_ROOT_PASSWORD=由凭据系统注入的root密码
docker compose -f docker-compose.app.yml build
docker compose -f docker-compose.app.yml up -d
docker compose -f docker-compose.app.yml ps
```

`docker-compose.app.yml` 不再提供数据库密码默认值；Compose 会在变量缺失时直接拒绝启动。
本地一键开发脚本使用的是 `docker-compose.mysql.yml`，与生产编排分开，不能把开发密码带入
正式环境。

Compose 内部 API 通过服务名 `mysql:3306` 连接数据库；宿主机端口不是容器间连接地址。

禁止将 `.env`、生产密码或占位密码打入镜像。

应用启动阶段会再次执行配置校验；`scripts\doctor.py` 只读输出相同规则，适合部署前
预检。启动校验失败时不会进入接收流量状态，避免依赖错误运行后才暴露。

启动门禁还会拒绝无效的运行边界，包括非正 `MAX_ROWS`、模型超时、每分钟限流值，以及
负数或 NaN/Infinity 的预算和 Token 单价配置。

认证模式下还必须配置至少 16 字符的 `METRICS_TOKEN`，避免 Prometheus 指标端点在生产环境
无保护暴露。

生产 `CORS_ORIGINS` 必须是明确的 `http://` 或 `https://` 来源列表，不允许通配符、路径、
查询参数或无 scheme 的主机字符串。

审计 JSONL 有默认字节上限和轮转数量上限，启动配置会拒绝过小或无效值；这只是单实例本地
保护，生产仍需接入集中日志、轮转、保留和访问审计平台。
观测汇总会读取配置范围内的轮转文件，本地日预算按 UTC 当日过滤；无有效时间戳的记录会保守计入，
避免历史坏数据导致额度低估。生产 Compose 默认将同一份脱敏 JSON 审计镜像到 stdout，且只有本地文件
写入成功后才输出；这只提供采集接口，不证明集中日志平台已经完成接入和权限验收。
审计事件必须保留 `tenant_id`、`user_id/actor_id`、`task_id` 和 `trace_id` 的关联；当前本地
实现已覆盖查询与审批主链，企业平台仍需校验字段映射、留存周期和跨租户查询权限。
本地审计导出工具要求按租户或任务限定范围并限制行数；生产导出仍必须由企业审计平台执行二次
授权、脱敏、留存和下载审计。
审计导出默认生成 SHA-256 sidecar，发布流水线也会将 CI 报告、SBOM、迁移计划、许可证盘点和镜像清单汇总为
`release-evidence.json`。证据索引出现未执行门禁、阻塞失败、空 SBOM、专项报告格式/计数矛盾或未提交的
已跟踪改动时，必须保持 `local_evidence_complete=false`。迁移未核对目标库、许可证未审批、镜像未全部
固定 digest、未完成扫描/签名/审批等状态单独进入 `production_blockers`，并令
`production_evidence_ready=false`。正式发布必须加 `--require-production-evidence`；即使严格模式通过，
索引本身仍不替代部署、业务验收和审批。
故障/恢复演练必须使用实际执行结果生成机器可读记录；只有全部检查通过且存在可校验证据文件时
才允许标为 `passed`。示例 JSON 不得作为生产演练证据。

`/ready` 在启用 Redis 限流、模型熔断、预算、导出或 Agent checkpoint 时会额外执行 Redis
连通性检查；默认内存模式不会强制依赖 Redis。健康响应只返回 backend、配置和连接状态，
不返回本机数据库路径、连接主机、库名、Redis URL 或凭据。


## 数据库连接池（2026-11-08）

此前 `app/mysql_db.py` 每次请求都新建 TCP 连接并重新认证，只用一个信号量限制并发；这是
9 月验收报告里的 P0-4，也是高并发下的第一个瓶颈。

现在控制面与只读查询各自使用一个有界连接池：

- 池大小 = `MYSQL_MAX_CONNECTIONS`（默认 10），超时 = `MYSQL_CONNECT_TIMEOUT_SECONDS`；
- 取出时 `ping(reconnect=False)` 校验，死连接、被调用方关闭的连接、超过
  `MYSQL_POOL_IDLE_TTL_SECONDS`（默认 300 秒）的空闲连接都会被丢弃并重建；
- 代码块内抛异常时连接直接关闭，不把可疑连接放回池；
- `query_connection()` 仍使用只读账号并保留 `SET SESSION TRANSACTION READ ONLY`。

实测（本机 MySQL 8.4，60 次并发查询、10 线程）：

| 指标 | 结果 |
|---|---|
| 新建数据库连接（`Threads_created` 增量） | **8**（原实现约为 60） |
| P50 / P95 | 130 ms / 304 ms |

边界：这是单机单进程内的池，不等于 PgBouncer/ProxySQL 之类的服务端池；多 Worker 部署时每个进程各有
一个池，总连接数为 `进程数 × MYSQL_MAX_CONNECTIONS`，需要按数据库上限调整。
