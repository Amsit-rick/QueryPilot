# 数据库兼容性验证

已有适配器的 backend 是 SQLite、MySQL 8.4、PostgreSQL 16、CSV、DuckDB，以及 Oracle、SQL Server、ClickHouse。
后三类远程 Gateway 均已在本机隔离实例完成真实只读链路验收，并具备主动取消、有界连接池、深度 Schema、
多 Schema / Database 限定名白名单和可选 TLS 失败关闭。Oracle 使用独立 Planner，SQL Server 使用
`SHOWPLAN_XML`，ClickHouse 使用 `EXPLAIN ESTIMATE`。这些证据不代表生产 HA、企业证书、容量或真实业务准确率验收。
配置、权限限制和真实测试入口见 [实验连接器说明](experimental-connectors.md)。

## Gateway 契约状态

`GET /api/v1/data-gateways` 会返回每个已授权 Gateway 的能力矩阵，测试工具可以先根据能力判断
是否应调用对应操作：

| 能力 | 当前契约含义 |
|---|---|
| `connection_test` | 可执行连接测试 |
| `schema` | 可读取受权限过滤后的表和字段 |
| `preview` | 可分页预览允许表的脱敏结果 |
| `query` | 可执行经过只读和字段权限校验的查询 |
| `cancel` | 是否支持取消正在执行的操作；Oracle、SQL Server、ClickHouse 为 `true` |
| `health` | 可读取健康状态 |
| `readonly` | Gateway 只读；当前契约固定为 `true` |

当前工厂支持的 backend 标识为 `sqlite`、`mysql`、`postgres`/`postgresql`、`csv`、`duckdb`、
`oracle`、`sqlserver` 和 `clickhouse`。远程连接器元数据分别暴露 `integration_status`、
`production_status` 和 `capability_gaps`；当前目录标记为 `accepted_local / not_validated`，不会冒充生产验收。
任何未支持的标识（例如 `unsupported`）都会在配置阶段返回
`E_GATEWAY_UNSUPPORTED`，不会被静默忽略，也不会回退到其他数据库。能力矩阵和错误码属于契约
验收，不等于目标数据库的生产兼容性证明；新增连接器仍需独立真实服务、只读拒绝和权限测试。
本机 2026-09-22：后端 `591 passed, 11 skipped, 1 warning`，其中三个新增真实实例用例未运行。

Windows cmd:

```cmd
cd /d D:\QueryPilot
docker compose -f docker-compose.mysql.yml up -d
docker compose -f docker-compose.mysql.yml ps
```

PostgreSQL 独立验收环境（Windows CMD）：

```cmd
docker compose -p query-pilot-postgres -f docker-compose.postgres.yml up -d
scripts\verify_postgres.cmd
set RUN_POSTGRES_TESTS=1
set POSTGRES_DSN=postgresql://query_readonly:query_readonly_dev@127.0.0.1:5433/query_agent
.venv\Scripts\python.exe -m pytest tests\test_postgres.py -q
```

2026-09-12 实测：MySQL 与 PostgreSQL 均通过 `customers JOIN sales`、Decimal 描述统计和专用 `query_readonly` 账号写入拒绝。Compose 中的开发密码只适用于隔离验收，生产环境必须替换并通过 Vault/KMS 注入。

生产环境必须由 DBA 创建只读账号，并从密钥管理服务注入密码；生产密码不得进入代码、迁移文件、日志或前端。

## 查询凭据与安全基线（2026-09-26）

- MySQL 控制面继续使用 `MYSQL_USER/MYSQL_PASSWORD`，业务查询必须配置独立的 `MYSQL_QUERY_USER/MYSQL_QUERY_PASSWORD`。认证生产配置若两组账号相同或缺少查询凭据，启动检查会阻止服务启动。
- PostgreSQL 业务查询优先使用 `POSTGRES_READONLY_DSN`；认证生产配置不能只依赖可写的 `POSTGRES_DSN`。
- SQLite、MySQL、PostgreSQL 查询均在执行前按真实方言通过 SQLGlot AST 校验，并使用命名参数绑定。驱动不得接收由字符串拼接形成的用户条件。
- 本地 MySQL 只读账号只获得白名单业务表的 `SELECT`；控制面表不授权。生产还需要 DBA 独立角色、数据库侧语句超时、连接来源/IP 白名单和审计平台验证。
- Oracle、SQL Server、ClickHouse 适配器仍为实验状态。Oracle 本机报告已验证健康、owner 级 Schema、
  非空主外键关系、预览、参数绑定、有界查询、CTE、独立 planner 成本估算和数据库写入拒绝，但 Shared Server
  登录后自启动方案、未认证的 Windows 11 + 19.3 组合以及未做企业阈值校准都不能作为生产标准；
  SQL Server、ClickHouse 仍无真实证据。
