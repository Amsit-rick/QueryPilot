# Oracle 独立 planner 成本估算 TDD 证据

## 用户旅程

作为数据平台管理员，我希望 Oracle 在执行查询前使用独立、最小权限 planner 账号生成执行计划，
从而在不授予业务查询账号任何写权限的前提下获得预计行数、成本、全表扫描和索引使用证据。

## RED

- `tests/test_remote_gateways.py -k oracle_cost_estimate`：`3 failed`，Oracle Gateway 不接受 `plan_connect`。
- `tests/test_connector_acceptance.py`：`3 failed, 5 passed`，报告仍为 v7、Oracle 仍只有八项证据且成本能力为 false。

## GREEN

| 保证 | 自动化/真实证据 | 结果 |
|---|---|---|
| planner 与业务查询连接分离 | `test_oracle_cost_estimate_uses_separate_planner_and_cleans_plan_rows` | PASS |
| 危险 SQL 在打开 planner 前被只读护栏拒绝 | `test_oracle_cost_estimate_rejects_writes_before_planner_connection` | PASS |
| 驱动异常不泄漏 planner 密码或主机 | `test_oracle_cost_estimate_hides_planner_errors` | PASS |
| 未显式开启的数据源不会因全局凭据而自动启用 | 远程 Gateway 相关回归 | PASS |
| 真实 Oracle 返回成本和计划操作 | `test_remote_cost_estimate[oracle]` | PASS |
| 执行后 PLAN_TABLE 清空 | 真实数据库检查 `COUNT(*) = 0` | PASS |
| planner 不能修改业务表 | 零行 `UPDATE` 返回 `ORA-01031` | PASS |

真实 Oracle 结果：预计行数、成本、全表扫描标记和计划操作均成功返回；报告契约升级为 v8，
Oracle 为 `9_passed`、退出码 0、`capability_gaps=[]`。报告不保存 SQL、参数、计划账号或密码。

相关回归 `118 passed`；完整后端 `967 passed, 33 skipped, 1 warning`。新增 skipped 来自未配置真实
Oracle 环境时的可选成本探针，不计为通过；本机真实探针已由 v8 报告单独证明。

## 权限边界

- `QUERYPILOT_RO` 继续只有业务表 `SELECT`，没有 PLAN_TABLE DML。
- `QUERYPILOT_PLAN` 只有 `CREATE SESSION`、允许业务表 `SELECT`，并作为自身 PLAN_TABLE 的 owner。
- `cost_estimate_enabled` 必须在具体 Oracle 数据源上显式开启，避免多数据源共享全局能力开关。
- SQL Server、ClickHouse 的成本估算仍未实现；Oracle 本机阈值不能直接作为企业生产阈值。

当前虚拟环境没有安装 `coverage`/`pytest-cov`，不提供虚构覆盖率数字。
