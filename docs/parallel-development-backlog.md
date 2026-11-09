# Query Pilot 剩余任务与并行开发分工

更新时间：2026-09-12

## 2026-09-15 集成状态更新

- `second` 分支已完成并推送应用里程碑：细粒度角色权限策略编辑、个人资料显示名称编辑（V014）。
- 当前开发卷已完成 V014 增量迁移和真实 API 链路验收；后端全量 `316 passed, 7 skipped, 1 warning`，前端构建通过，Windows 本地门禁 `5/5`。
- DG-01～DG-05、FE-01～FE-02、RA-01～RA-03、REL-01～REL-02 均已有代码和定向验证；`docs/integration-acceptance.md` 汇总证据。
- 剩余不是本地功能开发：企业 SSO/JWKS、正式密钥托管、真实生产数据源、容量/SLO、监控告警、备份恢复和故障演练需企业环境与负责人确认；Oracle 仍无真实服务验收。

## 1. 当前结论

下一阶段是 **阶段五：多数据源统一接入**。目标不是继续在 `app/main.py` 中增加数据库分支，而是建立稳定的 Data Gateway 契约，让 Agent、API 和任务系统只依赖统一接口。

当前企业语义层代码已经实现并通过测试、E2E 和真实 MySQL 验证，但尚未形成 Git 提交。新成员开始工作前，应先将该里程碑提交为共同基线；否则两人从不同未提交状态开发，会显著增加合并风险。

完成度只能按交付层级衡量：

| 目标 | 当前估算 | 剩余时间（两人并行） |
|---|---:|---:|
| 可完整演示的一轮版本 | 约 75% | 5–8 个有效开发日 |
| L1 部门试点 | 约 55% | 2–4 周，依赖企业 SSO 和真实数据源 |
| L2 受控生产 | 约 35% | 5–8 周以上，依赖监控、密钥、容灾和合规 |

## 2. 并行开发原则

1. 每个任务只允许一个文件所有者。需要修改他人所有文件时，先提交接口变更说明，不直接跨区修改。
2. 每个任务独立分支、独立工作树、独立提交；不要两个人共用同一未提交工作区。
3. 禁止功能分支直接修改共享装配文件：`app/main.py`、`frontend/src/App.vue`、`frontend/src/router.js`、`docker-compose.app.yml`。这些文件只由集成人在合并阶段修改。
4. 新能力通过独立模块、Router、Factory 或 Registry 暴露，集成人只做薄装配。
5. 数据库迁移文件只新增，不修改已经应用的版本；每人预留迁移号，避免重复编号。
6. 一个任务的测试不得依赖另一条未合并分支。外部服务使用 Fake Adapter 或契约测试替代。
7. 不提交 `.env`、密码、Token、数据库导出和用户未跟踪文档。

建议分支：

```text
codex/data-gateway-contract
codex/result-assets
codex/reliability-gates
codex/frontend-hardening
```

实际协作分支约定（2026-09-12）：新成员统一使用 `second`。远端 `origin/second` 已创建；后续 RA-01～RA-03、FE-01、REL-02 必须提交并推送到该分支，不再使用上述建议分支名拆分。

建议创建独立工作树（Windows CMD）：

```cmd
cd /d D:\QueryPilot
git worktree add ..\data_query_agent_gateway -b codex/data-gateway-contract main
git worktree add ..\data_query_agent_assets -b codex/result-assets main
```

## 3. 共享基线与冻结区

开始并行前必须满足：

- 企业语义层里程碑已提交并推送。
- `main` 上后端全量测试通过。
- 前端生产构建通过。
- `V012`、`V013` 已在集成 MySQL 应用。

当前冻结区：

| 路径 | 所有者 | 规则 |
|---|---|---|
| `app/domain/semantic.py` | 语义层维护者 | 阶段五不得修改 |
| `app/services/semantic_service.py` | 语义层维护者 | Data Gateway 只消费，不改契约 |
| `app/api/semantic_routes.py` | 语义层维护者 | 不与数据源任务混改 |
| `migrations/mysql/V012*`、`V013*` | 已应用迁移 | 永不修改，只能新增版本 |
| `app/main.py` | 集成人 | 功能开发者不直接修改 |
| `frontend/src/App.vue`、`router.js` | 集成人 | 页面通过独立 Route/Composable 交付 |

## 4. 可并行任务池

### DG-01：Data Gateway 核心契约

- 优先级：P0
- 状态：已实现并已推送 `second`
- 固定负责人：我（当前 Codex 开发任务）
- 依赖：语义层基线提交
- 可与 DG-02、RA-01、FE-01 并行
- 独占文件：
  - `app/domain/data_gateway.py`
  - `app/services/data_gateway_service.py`
  - `app/gateways/protocols.py`
  - `app/gateways/registry.py`
  - `tests/test_data_gateway_contract.py`
- 禁止修改：`app/main.py`、现有数据库驱动、前端文件
- 交付：统一定义 `test_connection`、`schema`、`preview`、`query`、`cancel`、`health`；统一错误码和方言元数据。
- 验收：使用 Fake Gateway 完成契约测试，不启动数据库也能运行。
- 实际验证：契约定向测试 `6 passed`；后端全量 `220 passed, 6 skipped, 1 warning`。公开入口为 `app.domain.data_gateway`、`app.gateways` 和 `DataGatewayService`。

### DG-02：SQLite 与 MySQL Gateway Adapter

- 优先级：P0
- 状态：已实现并已推送 `second`
- 固定负责人：我（当前 Codex 开发任务）
- 依赖：DG-01 的协议草案；可先基于约定接口开发
- 独占文件：
  - `app/gateways/sqlite_gateway.py`
  - `app/gateways/mysql_gateway.py`
  - `tests/test_sqlite_gateway.py`
  - `tests/test_mysql_gateway.py`
- 禁止修改：`app/db.py`、`app/mysql_db.py`；第一轮以适配器包装现有实现
- 交付：连接诊断、Schema、预览、只读查询、取消能力声明、健康状态。
- 验收：SQLite 必须本地通过；MySQL 契约测试默认 Fake，真实测试由集成环境通过 `RUN_MYSQL_TESTS=1` 执行。
- 实际验证：DG-01/02 定向测试 `13 passed`；后端全量 `228 passed, 6 skipped, 1 warning`。真实 SQLite 与 MySQL 均完成 health、schema、preview、query 验收；MySQL 默认只公开 `sales`、`customers`，内部用户和任务表不会进入 Schema 或预览入口。

### DG-03：PostgreSQL Gateway Adapter

- 优先级：P1
- 状态：已实现并已推送 `second`
- 固定负责人：我（当前 Codex 开发任务）
- 依赖：DG-01
- 独占文件：
  - `app/gateways/postgres_gateway.py`
  - `tests/test_postgres_gateway.py`
- 交付：与 SQLite/MySQL 相同契约，保留 PostgreSQL 方言和 Decimal 类型。
- 验收：契约测试和真实 PostgreSQL 只读测试；连接失败返回可操作诊断，不返回 DSN。
- 实际验证：Data Gateway 定向测试 `16 passed, 1 skipped`；启用真实环境后 PostgreSQL Adapter 与既有 PostgreSQL 验收合计 `5 passed`，覆盖 health、schema、preview、query、Decimal 保留和只读账号写入拒绝。后端全量为 `282 passed, 7 skipped, 2 failed, 1 warning`，两项失败均位于新成员负责的结果资产链路，本任务未修改或代为修复。

### DG-04：CSV 与 DuckDB Gateway Adapter

- 优先级：P1
- 状态：已实现并已推送 `second`
- 固定负责人：我（当前 Codex 开发任务）
- 依赖：DG-01；新增依赖前需单独确认
- 独占文件：
  - `app/gateways/csv_gateway.py`
  - `app/gateways/duckdb_gateway.py`
  - `tests/test_file_gateways.py`
- 安全边界：只允许配置目录下的文件，拒绝路径穿越、URL 和任意本机路径。
- 验收：临时目录契约测试；不访问用户真实文件。
- 实际验证：文件 Gateway 测试 `14 passed`，全部 Gateway 定向测试 `30 passed, 1 skipped`；后端全量 `299 passed, 7 skipped, 1 warning`。测试只使用 pytest 临时目录；路径穿越、URL、绝对路径、未知数据集、DuckDB 文件函数、ATTACH 和 COPY 均被拒绝。配置 CSV 物化为内存表后关闭 DuckDB external access，查询阶段不能访问其他本机或远程文件。

### DG-05：Gateway API 与 Agent 集成

- 优先级：P0
- 状态：已实现并已推送 `second`
- 固定负责人：我（当前 Codex 开发任务）
- 依赖：DG-01、DG-02
- 独占文件：
  - `app/api/data_gateway_routes.py`
  - `app/schemas/data_gateway.py`
  - `tests/test_data_gateway_api.py`
- 集成时才允许修改：`app/main.py`、`app/services/datasource_service.py`
- 交付：原 API 兼容保留；查询任务记录 gateway、datasource、dialect；Agent 不直接识别驱动。
- 验收：现有查询、审批、权限和任务测试不回归。
- 实际验证：新增 Gateway 列表、连接测试、健康、Schema、预览、管理员只读查询与取消 API；主 `/query` 的 Schema 和执行已切换到统一 Gateway Service。角色受限预览使用显式允许列投影，不读取后再丢弃禁用字段；任务步骤记录 gateway、datasource、dialect。真实 PostgreSQL 组合定向 `61 passed`，后端全量 `304 passed, 7 skipped, 1 warning`。

### RA-01：结果值归一化与容量预算

- 优先级：P0
- 状态：已实现并已推送 `second`，定向测试通过
- 固定负责人：新成员
- 无前置依赖
- 独占文件：
  - `app/services/result_preview_service.py`
  - `tests/test_result_preview_service.py`
- 交付：Decimal、日期、UUID、bytes、NaN/Infinity 的明确 JSON 规则；单元格、单行、总资产字节预算；记录截断原因。
- 禁止修改：`app/main.py`、任务 Repository
- 验收：纯单元测试覆盖特殊类型和超大文本，不依赖数据库。

### RA-02：统一结果资产契约

- 优先级：P1
- 固定负责人：新成员
- 依赖：RA-01
- 独占文件：
  - `app/domain/result_asset.py`
  - `app/services/result_asset_service.py`
  - `app/repositories/result_asset_protocol.py`
  - `tests/test_result_asset_service.py`
- 交付：表格、SQL、CSV、图表建议、Markdown 报告统一元数据；来源步骤、语义版本、数据版本、过期时间。
- 验收：任务/用户/租户绑定和下载再次授权有独立测试。

### RA-03：图表推荐与再次引用

- 优先级：P2
- 固定负责人：新成员
- 依赖：RA-02
- 独占文件：
  - `app/services/chart_recommendation_service.py`
  - `app/services/artifact_reference_service.py`
  - 对应测试文件
- 交付：确定性基础图表推荐；后续问题只能引用同租户同用户已授权的资产区域。
- 验收：不引入自动复杂图表，不把历史未授权数据重新送入模型。

### FE-01：前端会话与请求竞态治理

- 优先级：P1
- 状态：已实现并已集成
- 固定负责人：当前集成任务
- 无后端新接口依赖
- 独占文件：
  - `frontend/src/composables/useRequestScope.js`
  - `frontend/e2e/session-isolation.spec.js`
- 集成时由集成人修改现有 composable
- 交付：退出/切换账号后取消旧请求；查询取消、导出轮询和路由切换不回写旧状态。
- 验收：Playwright 模拟延迟响应，证明旧账号数据不会出现在新会话。
- 实际验证：`useRequestScope` 统一持有 `AbortController`；任务列表/详情、查询和导出请求传递 scope signal，退出账号时中止旧请求并递增会话版本；新增 `frontend/e2e/session-isolation.spec.js`，延迟旧账号任务响应场景 `1 passed`。

### FE-02：Gateway 管理页面

- 优先级：P1
- 状态：已实现并已推送 `second`
- 固定负责人：我（当前 Codex 开发任务）
- 依赖：DG-05 API 契约冻结；开发阶段使用 mock
- 独占文件：
  - `frontend/src/components/DataGatewayPage.vue`
  - `frontend/src/composables/useDataGateways.js`
  - `frontend/src/pages/DataGatewayRoute.vue`
  - `frontend/e2e/data-gateway.spec.js`
- 禁止修改：`App.vue`、`router.js`、`WorkspaceSidebar.vue`；由集成人最后挂载
- 验收：连接诊断、Schema、预览、健康状态和错误信息均有界面状态。
- 实际验证：管理员独立页面已接入连接测试、健康状态、Schema 表/字段、分页预览及加载/空数据/错误状态；前端生产构建通过，Gateway Playwright E2E `1 passed`，并验证 390px 移动视口无页面横向溢出。

### REL-01：任务恢复与幂等故障测试

- 优先级：P0（L1 前必须完成）
- 状态：已实现并已推送 `second`
- 固定负责人：我（当前 Codex 开发任务）
- 无 Gateway 实现依赖
- 独占文件：
  - `app/services/task_recovery_service.py`
  - `tests/test_task_recovery.py`
  - `docs/task-recovery-runbook.md`
- 交付：服务重启、检查点失败、资产写入失败、审批处理中断的明确恢复或失败策略。
- 验收：查询不得因检查点/资产失败重复执行；恢复操作有幂等键。
- 实际验证：恢复服务定向测试 `6 passed`；覆盖已有资产完成收敛、检查点/资产失败不重跑查询、审批等待/不确定终态、重复幂等调用和跨租户拒绝。后端全量 `310 passed, 7 skipped, 1 warning`。当前事件幂等不提供多实例并发唯一性，运行手册明确要求数据库唯一键或事务锁落地前不得自动批量并发恢复。

### REL-02：CI 安全与回归门禁

- 优先级：P1
- 固定负责人：新成员
- 无业务代码依赖
- 独占文件：
  - `.gitee/workflows/` 或项目最终选定的 CI 目录
  - `scripts/ci_verify.py`
  - `docs/ci-gates.md`
- 交付：后端测试、前端构建、关键 E2E、迁移静态检查、依赖和密钥扫描。
- 验收：高风险回归失败时阻止合并；测试报告可保存。

### OPS-01：生产配置、备份恢复与观测演练

- 优先级：P1（L1/L2 门禁）
- 固定负责人：暂不分配，等待企业运维目标
- 依赖：目标企业基础设施信息
- 独占文件：
  - `docs/operations/`
  - `scripts/operations/`
- 交付：RPO/RTO、备份恢复、迁移回滚、模型/Redis/数据库故障演练、指标和告警清单。
- 注意：没有真实演练证据时只能标为“文档完成”，不能标为生产验收通过。

## 5. 固定两人分工

以下分工在剩余开发周期内保持不变，不再按阶段或轮次重新分配。

| 负责人 | 完整负责范围 | 任务编号 |
|---|---|---|
| 我（当前 Codex 开发任务） | Data Gateway 全链路、Gateway 管理页面、任务恢复、共享装配和最终集成 | DG-01、DG-02、DG-03、DG-04、DG-05、FE-02、REL-01 |
| 新成员 | 结果归一化与结果资产全链路、前端会话竞态、CI 回归门禁 | RA-01、RA-02、RA-03、FE-01、REL-02 |
| 暂不分配 | 生产运维、RPO/RTO、告警和真实故障演练 | OPS-01 |

固定边界：

- 我不修改新成员独占的 `result_preview_service.py`、结果资产领域模块、`useRequestScope.js` 和 CI 文件。
- 新成员不修改 `app/gateways/`、`data_gateway_service.py`、Gateway 页面、任务恢复模块和共享装配文件。
- 新成员完成的功能由其提交独立分支；我只在最终集成提交中修改共享装配文件，不重写其实现。
- 新成员实际交付分支为 `second`；集成人只审查和合并 `origin/second` 上已提交的内容，不以共享工作区的未提交文件代替分支交付。
- 两人各自对自己名下任务完成开发、测试、审查和交付说明，不在每个小步骤重新分工。

## 6. 依赖关系

```text
语义层基线提交
  ├─ DG-01 ─┬─ DG-02 ─┬─ DG-05
  │         ├─ DG-03 ─┘
  │         └─ DG-04
  ├─ RA-01 ── RA-02 ── RA-03
  ├─ FE-01
  ├─ REL-01
  └─ REL-02

DG-05 API 契约冻结 ── FE-02
```

除图中箭头外，不允许人为增加跨任务依赖。

## 7. 每个任务的交付模板

每个任务提交时必须提供：

```text
任务编号：
变更范围：
独占文件：
公共接口：
数据库迁移：无 / Vxxx
安全边界：
执行的测试：
未执行的真实验收：
回滚方式：
后续集成动作：
```

提交信息包含任务编号，例如：

```text
feat(gateway): define data gateway contract [DG-01]
fix(frontend): isolate stale session requests [FE-01]
test(reliability): cover task recovery idempotency [REL-01]
```

## 8. 合并门禁

- 分支必须基于最新 `main`，合并前只允许 rebase 自己的分支。
- `git diff --check` 必须通过。
- 后端变更运行 `.venv\Scripts\python.exe -m pytest -q`。
- 前端变更在 `frontend` 目录运行 `npm run build` 和对应 Playwright 用例。
- 数据库变更必须使用新迁移号，并至少完成空库迁移和已有卷增量迁移之一；两者都通过才可宣称迁移完成。
- 权限、数据库查询、文件路径、外部模型或凭据相关变更必须做安全审查。
- 集成人解决共享装配文件冲突，功能开发者不得互相覆盖实现。

## 9. 暂不并行开发的事项

以下工作依赖企业决策或外部环境，不应在信息不足时自行实现：

- 企业 SSO/OIDC/JWKS 的实际供应商配置。
- Vault/KMS 或企业密钥平台接入。
- 正式数据源负责人、指标口径负责人和审批责任人。
- RPO、RTO、SLO、最大并发和数据保留周期。
- Oracle 真实验收环境。
- 合规、供应商和生产安全审批。

这些事项可以先准备接口和检查清单，但不能用模拟结果宣称完成。
