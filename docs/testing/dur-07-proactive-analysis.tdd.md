# DUR-07 Proactive Analysis TDD 记录

日期：2026-11-08

## 目标

在用户显式创建并启用计划后，由外部调度系统调用一次性 Runner 发起主动分析；每次运行重新执行当前身份、租户、数据源、预算、SQL 安全、审批和审计门禁，并支持暂停、恢复和永久关闭。

## 用户旅程

- 具备查询权限的用户可以创建草稿，但只有显式启用后才进入到期队列。
- 用户可以暂停、恢复和关闭自己的计划，不能访问其他账号或租户的计划。
- 运维系统必须显式使用 `--execute` 才能运行一次扫描。
- 多个 Runner 竞争同一计划时只有一个持有有效租约，旧租约不能提交成功结果。
- 一个计划的身份或成本估算失败不会中断整批扫描。

## RED

最初新增领域测试时，目标模块尚不存在：

```text
ModuleNotFoundError: No module named 'app.domain.proactive_analysis'
```

第一轮实现后得到 `3 failed, 14 passed`，失败原因是当前项目没有安装 `pytest-asyncio`。测试改用标准库 `asyncio.run`，不为本功能增加新的异步测试依赖，随后基础后端用例为 `17 passed`。

前端首次运行：

```text
1 failed
找不到 heading “主动分析计划”
```

证明组件存在但尚未接入路由、侧栏和全局上下文。

边界复核增加 stale lease 用例后：

```text
1 failed, 15 passed
report.completed == 1，预期为 0
```

根因是 Runner 在确认当前租约仍有效之前先记录了成功结果。修复后只有 `complete_claim` 成功才保留原状态；失效租约统一记录为 `failed / E_PROACTIVE_LEASE_STALE`。

成本估算隔离用例首次直接抛出 `RuntimeError: provider unavailable`，证明单计划异常会中断整批扫描。修复后该计划记录 `E_PROACTIVE_COST_ESTIMATE`、释放租约并推迟到下个间隔，后续计划继续执行。

## GREEN

最终 DUR-07 后端定向验证：

```text
28 passed
```

前端验证：

```text
1 passed  # 主动分析创建、启用、暂停、恢复、关闭和 390px 布局
3 passed  # 多轮对话 + 分支分析 + 主动分析联合回归
```

完整后端：

```text
1074 passed, 33 skipped, 1 warning
```

前端生产构建、Python `compileall`、`pip check` 和 `git diff --check` 通过。当前虚拟环境未安装 `pytest-cov` 或 `coverage.py`，因此没有声明覆盖率百分比；缺少覆盖率工具不被描述成 80% 已验证。

## MySQL 证据

`V027__create_proactive_analysis.sql` 已应用到本机现有 MySQL：

- `proactive_analysis_plans`
- `proactive_analysis_runs`
- `idx_proactive_plans_owner`
- `idx_proactive_plans_due`
- `idx_proactive_runs_plan`
- `idx_proactive_runs_task`

迁移表存在 `V027` 登记。真实仓储冒烟验证创建、状态转换、到期认领、租约完成、当日计数和运行记录读取全部通过；临时租户记录随后清理，剩余 0 条。未删除或重建数据卷。

## 测试规格

| 保证 | 证据 | 类型 |
|---|---|---|
| 草稿必须显式启用，关闭为不可恢复终态 | `tests/test_proactive_analysis.py` | 单元/服务 |
| 功能关闭时可保存草稿，但启用和恢复返回 409 | `tests/test_proactive_analysis.py` | API 集成 |
| 计划与运行记录按账号、租户隔离 | `tests/test_proactive_analysis.py` | API 集成 |
| 默认进入人工审批并生成稳定幂等请求 ID | `tests/test_proactive_analysis.py` | 服务集成 |
| 每日次数和预算达到上限时不执行查询 | `tests/test_proactive_analysis.py` | 单元 |
| 身份失效失败关闭并暂停计划 | `tests/test_proactive_analysis.py`、`tests/test_proactive_analysis_runtime.py` | 单元 |
| 并发认领只有一个持有者，过期租约可重领 | `tests/test_proactive_analysis.py` | 并发单元 |
| 旧 lease token 不能完成新认领 | `tests/test_proactive_analysis.py`、`tests/test_proactive_analysis_repository.py` | 单元/仓储 |
| 成本估算失败只影响当前计划 | `tests/test_proactive_analysis.py` | 单元 |
| MySQL 状态转换参数顺序和租户/账号作用域正确 | `tests/test_proactive_analysis_repository.py` | 仓储契约 |
| CLI 缺少 `--execute` 或功能关闭时失败关闭 | `tests/test_proactive_analysis_runner_cli.py` | CLI |
| 用户可在页面完成计划全生命周期 | `frontend/e2e/proactive-analysis.spec.js` | E2E |

## 未验收边界

本记录是本地代码、单实例 MySQL 和浏览器自动化证据，不代表企业调度平台、多机 Runner、数据库高可用、网络分区、真实告警通知、生产容量或真实业务准确率验收。API Key 非数字 subject 仍明确不支持。
