# Query Pilot — 企业级自然语言数据查询 Agent 平台

用户用中文提问，模型只负责生成候选 SQL，由确定性的权限、护栏、成本门禁、审批和审计决定这条 SQL 能不能执行；结果是可回放的数据任务，而不是一次性的聊天回答。

技术栈：FastAPI · Vue 3 · MySQL / PostgreSQL / SQLite（Oracle、SQL Server、ClickHouse 为实验连接器）· Redis · LangGraph / LangChain 可切换编排 · Pytest · Playwright。

## Windows 本地运行

新电脑先安装 Python 3.12、Node.js 22 和 Docker Desktop，然后在 CMD 执行一次：

```cmd
scripts\setup_local.cmd
```

首次安装会为 MySQL root、控制面和只读查询账号生成三套不同的随机密码，并为本地管理员生成独立的 `LOCAL_ADMIN_PASSWORD`，全部写入 Git 忽略的 `.env`；随后启用本地认证、把随包的完整企业演示数据幂等导入空 MySQL 库，并创建管理员 `admin`。管理员密码与任何数据库密码无关，查看方式：

```cmd
findstr /B "LOCAL_ADMIN_PASSWORD=" .env
```

本地开发默认选择 `mysql-main`，SQLite 演示库仍可切换。管理员密码只用于绑定 `127.0.0.1` 的隔离开发环境，不得用于生产或任何联网环境。

以后启动只执行：

```cmd
scripts\start_local.cmd
```

访问 `http://127.0.0.1:5173`；完整说明见 [`docs/local-start.md`](docs/local-start.md)。不配置 API Key
时，可测试显式 SQL 和确定性企业模板；不属于本地模板的任意自然语言问题需要在 `.env` 配置 `MODEL_API_KEY`，重启后由模型生成 SQL。

管理员可在“模型运营”中维护租户级提供商、健康状态和提示词版本。密钥只通过环境变量或 Vault/KMS 注入，页面只填写环境变量名；详见 [`docs/model-operations.md`](docs/model-operations.md)。

项目提供标准 MCP 2.x 只读服务和版本化 Skill 目录。MCP 当前开放 Schema、查询规划、任务状态、结果读取和 Skill 路由，不开放绕过审批的裸 SQL 执行；启动与客户端配置见 [`docs/mcp-skills.md`](docs/mcp-skills.md)。

本地已配置 `API_KEYS` 时，可在项目根目录直接执行 `scripts\start_mcp_stdio.cmd`；脚本不会回显密钥。

## 结果质量反思

查询执行后可运行确定性结果质量检查，并在必要时使用受控语义 Critic。默认 `AGENT_REFLECTION_MODE=shadow`、`AGENT_REFLECTION_LLM_ENABLED=false`：只执行规则并记录结构化建议，不改变查询结果，也不会把 Critic 未启用记成模型故障。

三种模式为 `off`（关闭结果反思）、`shadow`（只记录建议）和 `enforced`（应用修复、澄清、审批或失败动作）。`enforced` 必须存在通过的版本化评测报告，否则运行时自动降级到 `shadow`：

```cmd
.venv\Scripts\python.exe scripts\evaluate_reflection.py --output artifacts\reflection-eval.json
.venv\Scripts\python.exe scripts\evaluate_reflection_results.py --output artifacts\reflection-result-eval.json
.venv\Scripts\python.exe scripts\evaluate_reflection_security.py --report artifacts\reflection-security-eval.json
.venv\Scripts\python.exe scripts\build_reflection_release_gate.py --action artifacts\reflection-eval.json --result artifacts\reflection-result-eval.json --security artifacts\reflection-security-eval.json --output artifacts\reflection-release-gate.json
```

运行时只接受三份报告汇总后的组合发布门禁，旧的单报告不能开启 `enforced`。仓库内评测集是合成数据，只用于验证分类、结果级 Gold 对照、动作和安全门禁，不代表真实企业业务准确率。反思版本支持租户级 `draft/canary/active/retired` 生命周期、稳定百分比分桶、健康阈值回滚和脱敏审计。灰度、回滚、告警和 badcase 纳入流程见 [`docs/operations/reflection-rollout.md`](docs/operations/reflection-rollout.md)，实现任务与边界见 [`docs/reflection-mechanism-roadmap.md`](docs/reflection-mechanism-roadmap.md)。

启用 `AGENT_CHECKPOINT_ENABLED=true` 后，查询节点使用 Redis 恢复租约和单调 fencing token 提交版本化 checkpoint；旧持有者不能覆盖新恢复执行，快照不保存参数值或结果行。操作边界见 [`docs/task-recovery-runbook.md`](docs/task-recovery-runbook.md)。

多轮追问使用 `conversation-context-v2` 生成有界模型上下文：最近轮次、确定性 SQL lineage facts 和相关旧轮次都会按当前 Schema 权限重新过滤；结果行、参数、原始 SQL、失败 SQL 和模型 explanation 不进入 Prompt 历史。任务原始步骤和前端完整会话不会被压缩删除，配置与边界见 [`docs/conversation-compaction.md`](docs/conversation-compaction.md)。

已完成任务可从任意成功历史轮次创建独立分析分支。分支继承来源数据源和截至该轮次的安全上下文，但会重新执行当前账号权限、Schema、SQL 安全、成本、审批与审计；它拥有独立任务、步骤、结果资产和多轮对话，不覆盖主任务。工作台“假设对比”只展示脱敏摘要，详细契约、限制和边界见 [`docs/branch-analysis.md`](docs/branch-analysis.md)。

主动分析计划必须由用户先创建草稿再显式启用，默认每次运行进入人工审批。API 进程不启动隐藏定时线程；外部调度系统只能通过显式 `--execute` 的一次性 Runner 扫描到期计划。每次运行重新校验当前账号、租户、数据源、预算、SQL 安全、审批和审计，并使用租约避免多 Runner 重复认领。配置、操作和边界见 [`docs/proactive-analysis.md`](docs/proactive-analysis.md)。

## API

接口测试人员请先阅读 [`docs/api-test-guide.md`](docs/api-test-guide.md)；运行中的 Swagger UI 位于 `http://127.0.0.1:8000/docs`，OpenAPI JSON 位于 `http://127.0.0.1:8000/openapi.json`。

`POST /api/v1/query`

```json
{"question":"查询各区域销售额", "sql":"SELECT region, SUM(amount) AS total FROM sales GROUP BY region"}
```

所有查询必须是单条只读 `SELECT`，表和字段必须存在于 schema，默认最多返回 1000 行。用户输入条件必须通过命名参数传入，避免 SQL 拼接：

支持安全的条件表达式，例如 `CASE WHEN amount >= 10000 THEN 'high' ELSE 'normal' END`；不支持 `IF...ELSE` 过程块、存储过程或任意写操作。

```json
{"question":"查询华东销售额", "sql":"SELECT * FROM sales WHERE region = :region", "params":{"region":"华东"}}
```

查询超过配置的时间限制会返回 `E_TIMEOUT`。

## 测试

```powershell
pytest -q
```

M3 评测（Windows cmd）：`.venv\Scripts\python.exe -m app.eval`；评测指标和用例约定见 [`docs/evaluation.md`](docs/evaluation.md)。

真实业务评测（需要可用的 MySQL 演示库和模型密钥）：

```cmd
.venv\Scripts\python.exe scripts\evaluate_business_gold_mysql.py --check-gold
.venv\Scripts\python.exe scripts\evaluate_business_gold_mysql.py --output artifacts\business-gold-mysql.json
```

40 条中文业务问题在 50 万级企业演示库上执行，Gold SQL 与模型 SQL 走同一护栏和只读连接。2026-11-08 基线为
端到端结果一致率 72.5%～77.5%（中位数 73.8%，4 次重复运行）；开启枚举值接地后稳定在 **80.0%**（3 次运行）。
方法论、失败归因和"合成数据不等于企业真实业务验收"的边界见 [`docs/testing/business-gold-mysql-eval.md`](docs/testing/business-gold-mysql-eval.md)。

枚举值接地：管理员在 `data/datasources.json` 的 `value_columns` 里列出可接地的枚举列（如
`payments.status`），系统会采样真实存储值随上下文注入模型，避免把"已支付"当成参数值。
安全边界、配置项和配对实验结果见 [`docs/value-grounding.md`](docs/value-grounding.md)。

留出集：`data/business_gold_mysql_holdout.json` 额外提供 30 条**未参与调参**的问题。2026-11-08 结果：
关闭接地 86.7%，开启接地 93.3%（各 2 次，生成成功率 100%）。题面歧义修正、网络抖动门禁和剩余
能力缺口见 [`docs/testing/business-gold-mysql-eval.md`](docs/testing/business-gold-mysql-eval.md)。

压力测试（先启动后端，Windows cmd）：`.venv\Scripts\python.exe scripts\stress_runner.py --requests 50 --workers 10 --p95-max-ms 3000 --p99-max-ms 5000 --report artifacts\capacity-report.json`

本地生产安全证据与外部待验收项见 [`docs/operations/production-security-acceptance.md`](docs/operations/production-security-acceptance.md)。

部署前自检（Windows cmd）：

```cmd
.venv\Scripts\python.exe scripts\doctor.py
```

本地一键启动前也会自动执行交付包预检。若需要单独查看缺失文件、Docker 状态或端口冲突，可运行：

```cmd
.venv\Scripts\python.exe scripts\verify_local_bundle.py
```

该命令只输出通过项、警告和阻塞项，不输出 `.env` 中的密码、Token 或连接串。

启动后检查整套本地服务（API 健康、数据库就绪、前端入口）：

```cmd
scripts\check_stack.cmd
```

Prometheus 指标地址：`GET /metrics`，除请求、错误、平均耗时和模型成本外，还包含反思决策/修复、灰度回滚、checkpoint 恢复失败、预算耗尽、Critic 异常和反思延迟指标。Agent trace 只记录阶段、状态、耗时和稳定错误码，不记录 Prompt、参数、结果行或模型原始响应。

监控配置：`deploy/prometheus.yml` 可作为 Prometheus 抓取配置，`deploy/prometheus-alerts.yml` 提供需企业确认阈值的告警模板，`deploy/grafana-dashboard.json` 可导入 Grafana 展示请求量、错误量、平均延迟和模型成本。运行 `.venv\Scripts\python.exe scripts\validate_alert_rules.py` 可执行本地结构校验。

生产 HTTPS：使用企业网关或 Nginx 终止 TLS，参考 `deploy/nginx.conf`。将正式 CA 证书只读挂载到 `/etc/nginx/certs`，不要把证书和私钥提交到 Git；HTTP 到 HTTPS 的重定向应在企业网关层配置。

密钥生成（Windows CMD）：执行 `scripts\generate_secrets.cmd` 生成随机 JWT、指标和数据库密码；生产环境应写入 Vault/KMS 等密钥管理系统，不要提交 `.env` 或把密钥写入镜像。

Redis 导出队列（Docker，Windows cmd）：复制 `.env.example` 为 `.env` 后执行 `docker compose -f docker-compose.app.yml up --build`。该编排会启动 API、MySQL、Redis 和 `export-worker`；将 `EXPORT_EXECUTION_MODE=redis` 写入 `.env` 后，导出任务由 worker 异步处理。默认开发模式仍使用 inline，无需 Redis。

数据库备份与恢复（Windows CMD）：

```cmd
scripts\backup_mysql.cmd backups
scripts\restore_mysql.cmd backups\query_agent_备份文件.sql
```

恢复命令会二次确认，并会覆盖目标数据库数据；生产环境应将备份目录复制到独立存储。

导出文件持久化：Docker Compose 将宿主机 `data/exports` 挂载到 API 和 worker 容器，容器重建不会删除已生成的 CSV。生产环境应将该目录映射到独立磁盘或对象存储，并纳入备份策略。

生成可重复的演示数据（Windows cmd）：

```cmd
.venv\Scripts\python.exe scripts\seed_demo_data.py --backend both --customers 200 --sales 5000
```

PostgreSQL 验收（Windows CMD）：先执行 `docker compose -f docker-compose.postgres.yml up -d`，再运行 `scripts\verify_postgres.cmd`。该服务使用独立 5433 端口，不影响 MySQL 3307。验收容器会创建独立的 `query_readonly` 角色，应用连接应使用该角色；`query_app` 仅用于初始化和管理。生产环境必须通过环境变量或 Vault 注入随机密码，并替换验收脚本中的开发密码。

## SQL 安全能力

自然语言生成 SQL 时，模型返回受限 `SqlPlan`：SQL 只包含 `:name` 占位符，参数值独立绑定；Prompt 注入前置检测只作为辅助信号，最终安全边界仍是方言 AST 校验、表字段白名单、行数上限和数据库只读账号。

`GET /metrics` 还包含累计返回行数、截断查询数和大结果查询数；这些指标衡量应用返回结果，不等同于数据库实际扫描行数。查询执行前已对 SQLite/MySQL/PostgreSQL 接入方言化 `EXPLAIN` 成本门禁，阈值需按企业目标库基线校准；不支持 EXPLAIN 的实验连接器可通过 `QUERY_COST_REQUIRE_EXPLAIN=true` 阻断。主键/外键关系可通过 `/api/v1/data-gateways/{id}/schema-details` 查看，详情见 `docs/query-cost-and-relationships.md`。
