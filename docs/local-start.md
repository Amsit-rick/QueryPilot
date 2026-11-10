# 本机安装与一键启动

新电脑首次安装需要 Python 3.12、Node.js 22 和 Docker Desktop。Docker Desktop 启动后，在项目根目录执行：

```cmd
scripts\setup_local.cmd
```

首次安装脚本会创建虚拟环境、安装 Python/npm 依赖、从开发 Compose 生成 Git 忽略的 `.env`、
启用本地认证、启动并等待 MySQL、应用迁移、把随包 SQLite 中的完整企业演示表幂等导入空 MySQL、创建本地 `admin` 管理员，最后启动前后端。
全新安装会随机生成互不相同的 `MYSQL_ROOT_PASSWORD`、`MYSQL_PASSWORD`、`MYSQL_QUERY_PASSWORD`，
以及本地管理员专用的 `LOCAL_ADMIN_PASSWORD`；Compose 只引用 `.env`，仓库不再保存固定数据库密码。
管理员密码不是数据库密码，查看方式：`findstr /B "LOCAL_ADMIN_PASSWORD=" .env`。
本地开发默认数据源为 `mysql-main`；SQLite 演示库仍可在工作台下拉框中选择。用 `admin` 和该密码登录
后可验证完整管理员工作区；该密码只允许用于隔离测试机，不得复制到生产环境。

没有 `MODEL_API_KEY` 时，显式 SQL 和内置确定性企业问题仍可运行；其他自然语言问题会返回
`E_MODEL_CONFIG`，按提示在 `.env` 配置模型密钥并重启，不会再误报为“查询后处理失败”。

脚本重复执行时不会重置已有管理员密码。如需在本地测试机显式重置，先设置新密码再执行：

```cmd
set LOCAL_ADMIN_PASSWORD=新的本地测试密码
.venv\Scripts\python.exe scripts\local_setup.py bootstrap-admin --reset-existing
```

首次安装完成后，每次只需要：

```cmd
scripts\start_local.cmd
```

脚本会启动 MySQL、等待数据库 ready、执行本地交付包预检，再打开后端窗口和前端窗口。后端配置自动读取项目根目录的 `.env`，不需要每次手动执行 `set MYSQL_*`。
若 `.env`、虚拟环境、前端依赖或 Docker 缺失，启动脚本会直接提示重新运行首次安装，不再打开必然失败的窗口。

需要在同一页面使用全部已适配数据库时，将 `.env` 中的 `START_ALL_DATASOURCES` 设为 `true`，并完成 PostgreSQL、Oracle、SQL Server、ClickHouse 的连接配置。`start_local.cmd` 会调用 `scripts\start_all_datasources.cmd`；该脚本会幂等启动 MySQL、PostgreSQL、SQL Server、ClickHouse，复用本机 Oracle 服务，并分别运行 SQL Server、ClickHouse 的演示 Schema 迁移。最后执行 `verify_configured_datasources.py`，验证必须实际通过连接、日期字段探测、`sales` 明细查询和月度聚合查询，API 才会启动。SQL Server 与 ClickHouse 占用内存较高，16GB 开发机应先停止无关项目容器，避免 Docker Engine 因同时恢复多组服务而重启。

PostgreSQL 启动后会先等待 `pg_isready` 成功，再通过 `seed_multimonth_demo_data.py` 幂等补齐最近 12 个月、4 个区域的 48 条分析演示数据。脚本只使用 `910000+` 的保留 ID，不覆盖原始演示记录；重复启动不会重复累加。Oracle 不自动执行 SYSDBA 操作，因为配置项可能指向远程或共享实例。本机 Oracle 19c 需要补齐同一批数据时，管理员可在确认 PDB 和 Schema Owner 后显式执行：

```cmd
set ORACLE_HOME=D:\Oracle\product\19c\dbhome_1
.venv\Scripts\python.exe scripts\seed_multimonth_demo_data.py --months 12 --oracle-sysdba --oracle-pdb ORCLPDB1 --oracle-schema-owner QUERYPILOT_OWNER
```

`ORACLE_HOME` 应替换为当前电脑的真实 Oracle Home，也可以改用 `--oracle-home` 显式传入。需要固定演示月份以复现实验时，可额外传入 `--end-month YYYY-MM`。默认值为执行当月，因此日常启动产生的是滚动 12 个月窗口。

也可以单独运行只读预检（输出机器可读 JSON，不会显示密码或 Token）：

```cmd
.venv\Scripts\python.exe scripts\verify_local_bundle.py
```

预检会检查必需文件、MySQL 配置、Docker Desktop、Compose 语法和 `3307/8000/5173` 端口。
MySQL 端口必须已经监听；API 或前端端口被占用会阻止启动，避免重复打开旧服务窗口。

启动后可执行运行态检查，确认 API 存活、数据库就绪和前端入口都能访问：

```cmd
scripts\check_stack.cmd
```

该检查只记录 HTTP 状态码和内容类型，不输出响应正文、密码或 Token；任一关键检查失败都会返回退出码 1。

访问地址：

- 前端：`http://127.0.0.1:5173`
- API 文档：`http://127.0.0.1:8000/docs`

不要同时执行 `docker-compose.app.yml`，否则会启动另一套 API，造成端口和数据库配置混淆。

前端开发服务器默认只监听本机地址，适合“在另一台电脑上安装并测试”。如果要让局域网其他设备
直接访问这台机器上的前端，需要额外把 Vite host 改为受控的局域网地址，并同步配置后端 CORS；
当前安装脚本不自动开放局域网访问，避免把开发 API 暴露到网络。

## SQL 安全回归与查询账号

首次安装会生成独立的 MySQL root、控制面和业务查询密码。日常启动只补齐缺失的数据库凭据，不会覆盖已有模型密钥、数据库地址或已有密码，并会幂等收敛只读账号的白名单表权限。为兼容旧数据卷，旧 `.env` 缺少 root 密码时会暂时沿用已有控制面密码，预检会给出密码复用警告，而不会在普通启动中自动轮换导致数据卷失联。需要拆分旧密码时，必须使用下述显式轮换流程。新建 MySQL 容器只将配置端口绑定到 `127.0.0.1`。

### 旧数据卷凭据轮换

先关闭 Query Pilot API 和导出 Worker，保留 MySQL 容器运行。以下命令在项目根目录的 Windows CMD
执行；命令只把 `.env` 中的现有值放入当前进程环境，不会打印密码：

```cmd
for /f "tokens=1,* delims==" %A in ('findstr /B "MYSQL_ROOT_PASSWORD=" .env') do set "MYSQL_ROOT_PASSWORD=%B"
if not defined MYSQL_ROOT_PASSWORD for /f "tokens=1,* delims==" %A in ('findstr /B "MYSQL_PASSWORD=" .env') do set "MYSQL_ROOT_PASSWORD=%B"
set MYSQL_CONTAINER=data_query_agent-mysql-1
scripts\backup_mysql.cmd backups
```

从命令输出中取得本次新生成的 `.sql` 文件名，先验证备份并创建、复核 SHA-256 清单：

```cmd
scripts\verify_backup_mysql.cmd backups\query_agent_时间戳.sql
.venv\Scripts\python.exe scripts\operations\backup_manifest.py create backups\query_agent_时间戳.sql
.venv\Scripts\python.exe scripts\operations\backup_manifest.py verify backups\query_agent_时间戳.sql
```

先执行不修改数据库的 dry-run，再把同一备份清单交给正式轮换：

```cmd
.venv\Scripts\python.exe scripts\rotate_local_mysql_credentials.py --dry-run --report artifacts\mysql-credential-rotation-dry-run.json
.venv\Scripts\python.exe scripts\rotate_local_mysql_credentials.py --apply --backup-manifest backups\query_agent_时间戳.sql.manifest.json --report artifacts\mysql-credential-rotation.json
```

工具依次轮换只读查询、控制面和 root 账号，每一步使用新密码建立连接验证；全部成功后才原子更新
`.env`。数据库修改或 `.env` 写入失败时会反序恢复已修改账号。报告只记录账号类别、步骤状态和时间。
若报告状态为 `rollback_failed`，不要重启或重建容器，应保留备份和现场状态并人工恢复。

可单独运行版本化 SQL 安全回归（Windows CMD）：

```cmd
.venv\Scripts\python.exe scripts\evaluate_sql_security.py --report artifacts\sql-security-eval.json
```

报告中的通过率仅表示固定攻击语料的回归结果，不代表所有未知 SQL 攻击的拦截比例。
