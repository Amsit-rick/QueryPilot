# 实验数据库连接器（DB-01+）

Oracle、SQL Server、ClickHouse 已有独立 Gateway 和驱动连接实现，默认不开启。
2026-11-07 已在本机 Oracle Database 19c 19.3 Enterprise Edition 上完成八项真实探针；
SQL Server、ClickHouse 仍没有真实实例证据。该结果只证明当前隔离开发环境，不代表生产兼容。
Gateway 列表的 `metadata.maturity` 固定为 `experimental`，静态元数据不会因为一次健康检查或
本地报告自动升级；真实验收状态以脱敏 acceptance 报告为准。

## 安装和配置

Windows CMD，项目根目录：

```cmd
.venv\Scripts\python.exe -m pip install -r requirements-connectors.txt
```

驱动按需延迟导入，未安装可选依赖不会影响既有 SQLite/MySQL/PostgreSQL。
Oracle 使用 python-oracledb Thin 模式；SQL Server 使用 pymssql/FreeTDS；
ClickHouse 使用 clickhouse-driver 原生协议，默认端口 9000，不是 HTTP 8123。

在本机 `.env` 或启动进程的环境变量中配置以下字段。不要将实际凭据写入注册表、提交或发回。

| backend | 配置项 |
|---|---|
| `oracle` | `ORACLE_DSN`（host:port/service 或完整 Oracle Connect Descriptor）、`ORACLE_USER`、`ORACLE_PASSWORD`、`ORACLE_SCHEMA_OWNER`（业务底表 owner）、`ORACLE_PLAN_USER`、`ORACLE_PLAN_PASSWORD`（独立计划评估账号） |
| `sqlserver` | `SQLSERVER_HOST`、`SQLSERVER_PORT`（默认 1433）、`SQLSERVER_DATABASE`、`SQLSERVER_USER`、`SQLSERVER_PASSWORD` |
| `clickhouse` | `CLICKHOUSE_HOST`、`CLICKHOUSE_PORT`（默认 9000）、`CLICKHOUSE_DATABASE`、`CLICKHOUSE_USER`、`CLICKHOUSE_PASSWORD`、`CLICKHOUSE_SECURE` |

`GATEWAY_CONNECT_TIMEOUT_SECONDS` 默认 5 秒，`GATEWAY_QUERY_TIMEOUT_SECONDS` 默认 30 秒。
生产 TLS、服务器证书和账号权限仍需目标环境验证；不要把 SQL Server 的 read-only 连接意图
误认为账号权限控制。ClickHouse TLS 通常使用 9440，并配置 `CLICKHOUSE_SECURE=true`。

在 `data/datasources.json` 的 `datasources` 数组追加需要测试的条目，例如：

```json
{
  "id": "oracle-test",
  "name": "Oracle 实验库",
  "backend": "oracle",
  "schema_owner": "BUSINESS_OWNER",
  "cost_estimate_enabled": true,
  "readonly": true,
  "roles": ["admin", "analyst"],
  "allowed_tables": ["sales", "customers"]
}
```

另两种使用 `backend=sqlserver`、`backend=clickhouse`，并使用不同 id。
每种 backend 当前使用一组进程配置，多个同 backend 条目仍连接同一实例，不能将其误当成
独立租户凭据。`allowed_tables` 省略时仅允许 sales/customers；空数组表示没有允许表。
角色策略继续使用现有 `data/access_policy.json`。控制面 `DATABASE_BACKEND` 保持原值，
本批扩展的是查询数据源，未迁移任务、认证或审批数据库。

## 支持边界

- 主查询和只读规划接收目标方言及 `:name` 参数，先转换成现有权限链路使用的规范 SQL，
  执行前由 SQLGlot 转换回目标方言。值交给数据库驱动绑定，不拼接到 SQL。
- 只接受单条 SELECT、常用聚合/筛选/排序/分页和受支持函数；拒绝写入、SELECT INTO、
  锁定读取、跨库/跨 schema/数据库链接、表函数、未知函数和查询设置覆盖。
- 支持受限的非递归 CTE，可用于先按业务粒度预聚合后再 JOIN；递归、自引用、超过 8 个 CTE、
  重复/覆盖业务表名称和不明确输出列会拒绝。暂不承诺任意厂商函数、LOB/特殊类型、复杂方言转换
  和存储过程支持。不能无损转换的 SQL 会拒绝，不会静默回退数据库。SQL Server 字符串/标识符内
  的驱动占位符文本 `%(` 会拒绝。
- Oracle 当前读取当前用户的表/视图；SQL Server 当前读取默认 schema；ClickHouse 当前读取配置数据库。
- Oracle 执行前设置只读事务；ClickHouse 使用服务端 `readonly=1`；SQL Server 必须由管理员
  配置仅 SELECT 的账号。所有数据库均应使用仅能访问指定业务对象的只读账号。
- 查询结果上限最多 10000 行、调用层 offset 最多 100000，HTTP 服务仍有更小的现有上限。
  执行 SQL 同时限制取回行数；截断使用多取一行判断。取消能力明确为 false。

## 真实验收

仅针对独立测试实例，先由数据库管理员准备当前 schema 下的 `sales` 表，至少两行合成数据，
字段包含 id、region、amount；region 包含中文，amount 建议 Decimal。账号须能 SELECT，不能写。
测试不会建表、导入数据或更改授权。末尾会执行 `WHERE 1=0` 的零行写入探针，要求数据库明确
返回权限/只读错误，不能仅靠应用护栏证明数据库账号只读。

Windows CMD，选择相应标志，可一次开启多个：

```cmd
set RUN_ORACLE_TESTS=1
set RUN_SQLSERVER_TESTS=1
set RUN_CLICKHOUSE_TESTS=1
.venv\Scripts\python.exe -m pytest tests\test_remote_gateways_live.py -q
```

未设置标志时测试明确 skipped，不算通过。验收需记录数据库/驱动版本、权限配置、Schema、
中文/Decimal、绑定参数、分页、写入拒绝和失败错误码。真实数据或凭据不得进入测试报告。

### 自动化验收入口

为避免手工运行时把 skipped 或未配置误认为通过，使用统一入口生成脱敏 JSON 报告：

```cmd
.venv\Scripts\python.exe scripts\connector_acceptance.py --connector all --report artifacts\connector-acceptance.json
```

也可以只验收一种连接器：

```cmd
.venv\Scripts\python.exe scripts\connector_acceptance.py --connector oracle
```

脚本会读取 `.env` 和进程环境中的配置，但只记录配置是否齐全、驱动是否安装、是否实际执行、
pytest 汇总计数、能力覆盖矩阵和阻塞原因；不会写出主机、用户名、密码、DSN、查询结果或原始异常。缺少配置或
驱动时 `executed=false`，测试 skipped、失败、超时和退出码异常均为 `passed=false`，进程返回非零。
只有所选连接器的真实 live case 都执行且至少各有一条通过、没有 skipped/failed/error 时，报告才会写入
`external_validation=passed`；报告同时保留退出码和耗时。使用 `--connector all` 时，三种连接器都必须满足条件。
本机 Oracle 已有真实实例报告；SQL Server、ClickHouse 未配置时仍必须保持阻塞，不能用 Oracle 的
通过结果替代另外两种数据库，也不能将单机开发验收写成生产兼容证明。

报告 v8 的基础 `evidence` 字段覆盖 health、schema、schema_details、preview、parameter_binding、query、
cte_preaggregation 和 readonly_rejection；Oracle 额外执行 `cost_estimate`，因此 Oracle 为九项探针，
SQL Server、ClickHouse 仍为八项，
并通过 `capability_implementation` 单独报告适配器已经实现的能力。
Oracle 与 SQL Server 的实验适配器已增加数据库外键元数据读取，并按允许表/字段过滤、按约束合并复合关系；
其中 Oracle 可按数据源配置 `schema_owner`，使用参数绑定读取 `ALL_TAB_COLUMNS/ALL_CONSTRAINTS`，查询会话切换到
经过标识符白名单校验的 owner。v7 的关系型连接器 `schema_details` 探针要求至少存在一条允许范围内的真实外键，
空关系列表不能再作为通过证据；
ClickHouse 当前没有统一外键元数据契约，因此仍报告 `relationships` 能力缺口。Oracle 的成本估算使用独立
planner 账号执行 `EXPLAIN PLAN`，读取后立即清理自己的 PLAN_TABLE；业务查询账号不获得 PLAN_TABLE DML。
SQL Server、ClickHouse 的 `cost_estimate` 仍显式返回不支持。
每项 evidence 作为独立 pytest 探针执行。单项失败只会把对应 evidence 标记为 false，其余已通过证据仍保留；
整体 `passed` 仍要求所选连接器的全部探针通过。`--timeout-seconds` 对每一个独立探针生效。原始 pytest 输出和数据库异常
不会写入报告。`cte_preaggregation` 会在目标实例上实际执行一个按区域聚合的非递归 CTE，并继续使用命名参数绑定；
只有该查询真实返回结果，报告才会把这一证据标为通过。`capability_gaps` 只表示适配器尚未实现或真实 live case 尚未覆盖的能力；`capability_implementation` 与
`evidence` 分开记录，不会把代码存在误报成真实实例验收通过。

### 2026-11-07 Oracle 本机真实验收

- 目标为 Oracle Database 19c Enterprise Edition 19.3，CDB/PDB 为 `ORCL` / `ORCLPDB1`，字符集为
  `AL32UTF8`，驱动为 `python-oracledb 3.4.2` Thin 模式。
- 使用独立 owner 保存底表与主外键，查询账号只持有底表 `SELECT`；数据源显式配置
  `schema_owner=QUERYPILOT_OWNER`，元数据从 `ALL_TAB_COLUMNS/ALL_CONSTRAINTS` 读取，查询会话切换到该 owner；
  零行 `UPDATE` 由数据库返回 `ORA-01031`，不是只靠应用护栏拒绝。
- `scripts/connector_acceptance.py --connector oracle` 实际执行九项探针，结果为 `9_passed`、
  `external_validation=passed`；脱敏报告位于 `artifacts/connector-acceptance-oracle.json`。
- 当前 Windows 主机活动计算机名与 TCP/IP 主机名不一致，19.3 dedicated handoff 返回
  `TNS-00534 / Windows 10022`。本次使用仅绑定回环地址的 Shared Server Connect Descriptor 和
  1522 `QUERYLISTENER` 完成验收，并通过当前用户 `HKCU Run` 在登录后静默自启动。它不是 Windows
  服务，用户登录前不可用，也不是生产部署方案；正式 1521 dedicated 模式仍需管理员权限配置
  `USE_SHARED_SOCKET`、重启服务并复验。
- `schema_details` 真实探针已要求非空关系，并从 owner 元数据召回 `SALES.CUSTOMER_ID → CUSTOMERS.ID`；
  非法 owner 会在打开数据库连接前以 `E_GATEWAY_CONFIG` 拒绝，避免通过 owner 配置注入标识符。
- Oracle `cost_estimate` 已由独立 `QUERYPILOT_PLAN` 账号完成真实验收。该账号只有 `CREATE SESSION`、
  对允许业务表的 `SELECT`，以及自身 PLAN_TABLE 的所有者权限；对业务表 `UPDATE` 仍返回 `ORA-01031`。
  成本探针验证预计行数、成本、全表扫描/索引标记和执行后 PLAN_TABLE 清理，Oracle 报告不再包含 capability gap。


### 2026-11-08 ClickHouse 本机真实验收

- 目标为 ClickHouse 24.8.14.39（官方镜像 24.8，经可达的镜像代理拉取），容器 `qp-clickhouse` 仅绑定
  `127.0.0.1:9000/8123`；库 `demo`，表 `sales(id, customer_id, region, amount Decimal(12,2))` 与
  `customers(id, name)`，中文区域与 Decimal 已实测可读。
- 只读账号 `qpro` 仅持有 `GRANT SELECT ON demo.*`；零行 `INSERT` 由服务端拒绝，错误码 497，
  不是只靠应用护栏。
- 发现并修复连接器缺陷：原实现在同一次连接中同时下发 `readonly=1` 与 `max_execution_time`，
  ClickHouse 在 readonly 模式下拒绝修改任何 setting，导致健康检查和全部查询失败（本机首次验收
  `8_failed`）。修复为只下发 `max_execution_time`，只读改由数据库授权保证，并补
  `tests/test_remote_gateways.py::test_clickhouse_connection_does_not_combine_readonly_with_timeout`。
- `scripts/connector_acceptance.py --connector clickhouse` 实际执行八项探针，结果 `8_passed`、
  `external_validation=passed`；脱敏报告为 `artifacts/connector-acceptance-clickhouse.json`。
- 验收完成后容器已停止以释放内存；复验前执行 docker start qp-clickhouse 并确认 .env 中的 CLICKHOUSE_* 仍有效。
- 能力边界：ClickHouse 没有统一外键元数据契约，`relationships` 与 `cost_estimate` 仍是显式能力缺口，
  不因本次验收改变。


### 2026-11-08 SQL Server 验收准备与阻塞

- 新增 `docker-compose.sqlserver.yml`：镜像 `mcr.microsoft.com/mssql/server:2022-latest`，SA 密码
  通过 `MSSQL_SA_PASSWORD` 注入（缺失即失败），端口只绑定 `127.0.0.1`，带健康检查。
- 新增 `scripts/provision_sqlserver.py`：幂等创建 `acceptance` 库、`customers`/`sales` 表、
  外键 `fk_sales_customer`、只读登录与 `GRANT SELECT ON SCHEMA::dbo`；`--dry-run` 不连接数据库且
  脱敏密码；完成后再用只读账号验证"能读、写入被拒"。种子数据含中文区域与 Decimal。
- `.env.example` 补齐 `SQLSERVER_*` 与 `CLICKHOUSE_*` 占位项；此前模板只有 Oracle，实验连接器无法
  仅凭模板完成配置。
- 当前阻塞（截至 2026-11-08）：本机没有 SQL Server 实例；Docker Hub 与 MCR 的大层下载在本机网络下
  多次卡死（20 分钟以上无字节进展），daocloud 代理只镜像 Docker Hub。因此 SQL Server **没有真实
  实例证据**，DB-01+ 维持 `2/3`，不宣称通过。
- 环境就绪后只需三条命令：

```cmd
set MSSQL_SA_PASSWORD=由凭据系统注入的密码
docker compose -f docker-compose.sqlserver.yml up -d
.venv\Scripts\python.exe scripts\provision_sqlserver.py
.venv\Scripts\python.exe scripts\connector_acceptance.py --connector sqlserver
```

- 验证：`tests/test_provision_sqlserver.py` 5 passed；`docker compose -f docker-compose.sqlserver.yml config --quiet` 通过；`--dry-run` 不泄露密码。


### 2026-11-08 SQL Server 本机真实验收（完成）

- 目标为 Microsoft SQL Server 2022（16.0 RTM-CU27-GDR），容器 `data_query_agent-sqlserver-1`
  只绑定 `127.0.0.1:1433`；镜像通过官方 MCR 拉取（digest `sha256:cf8f0ab2...`），本机链路约
  0.15 MB/s，完整镜像 2.35 GB 耗时较长但已成功。
- 使用 `scripts/provision_sqlserver.py` 幂等创建 `acceptance` 库、`customers`/`sales` 表、
  外键 `fk_sales_customer`、只读登录 `qpro_ro`（仅 `GRANT SELECT ON SCHEMA::dbo`）；种子数据含中文
  区域与 Decimal，零行 `UPDATE` 由数据库权限拒绝，不是应用护栏。
- `scripts/connector_acceptance.py --connector sqlserver` 实际执行八项探针，结果 `8_passed`、
  `external_validation=passed`；唯一能力缺口是尚未实现的 `cost_estimate`。脱敏报告为
  `artifacts/connector-acceptance-sqlserver.json`。
- 验收后容器已停止以释放内存；复验执行 `docker compose -f docker-compose.sqlserver.yml up -d`，
  再跑 `scripts/provision_sqlserver.py` 与验收命令即可。
- 顺带修复 `scripts/connector_acceptance.py` 在中文 Windows 下用 GBK 解码 UTF-8 子进程输出、
  导致整轮验收崩溃的问题（改为 UTF-8 + `errors="replace"`）。修复后 SQL Server 仍为 `8_passed`。
- Oracle 在 2026-11-08 复验时先失败，根因是实例的 `local_listener` 仍指向已停止的 1522 共享监听，
  1521 上没有服务注册。改为 `127.0.0.1:1521` 并执行 `ALTER SYSTEM REGISTER` 后，`ORCL` 与
  `ORCLPDB1` 均为 READY、DEDICATED handler 可用；`.env` 的 `ORACLE_DSN` 同步改为
  `127.0.0.1:1521/ORCLPDB1`，九项探针复验 `9_passed`、`external_validation=passed`。
  这属于本机 Oracle 监听配置漂移，不是连接器代码问题。
