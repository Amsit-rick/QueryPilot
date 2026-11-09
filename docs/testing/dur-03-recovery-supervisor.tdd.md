# DUR-03 Recovery Supervisor TDD 证据

日期：2026-11-07

## 1. 来源与完成标准

来源为 `项目改造计划书.md` 的 Durable Agent 专项 DUR-03。完成标准：有界扫描陈旧非终态任务；按任务获取租约；Redis 与 MySQL 双层 fencing；联合 checkpoint、请求准入和工具重放策略决策；不确定副作用失败关闭；跨租户、批次上限、错误隔离和恢复事件幂等可验证；默认不自动启动。

## 2. 用户旅程

- 作为运维人员，我希望服务重启后能一次性扫描陈旧任务，使已有持久化证据的任务安全收敛。
- 作为平台负责人，我希望旧 worker 即使继续运行，也不能覆盖新 worker 已接管的任务状态。
- 作为安全负责人，我希望结果未知的 SQL、审批和导出副作用不会因为恢复流程被盲目重放。
- 作为租户管理员，我希望恢复扫描可以跨租户工作，但每个任务的读取、准入和状态更新仍严格使用其原 tenant_id。

## 3. RED 证据

首次命令：

```cmd
.venv\Scripts\python.exe -m pytest tests\test_recovery_supervisor.py -q
```

首次结果：测试收集失败，`ModuleNotFoundError: No module named 'app.services.recovery_supervisor_service'`。

后续独立 RED：

| 行为 | RED 结果 |
|---|---|
| 数据库 fencing | `AttributeError: TaskService has no attribute claim_recovery_fence` |
| 显式 runtime 门禁 | `ImportError: cannot import recovery_supervisor_runtime` |
| 接管竞态错误隔离 | 旧 token 的 `E_RECOVERY_FENCING_STALE` 被误当成 checkpoint 错误并中断批次 |
| Redis 获取异常隔离 | 获取租约时的非领域异常最初会中断整批 |
| 恢复事件数据库 fencing | `TaskService` 最初没有按当前 token 原子写 Supervisor 事件的接口 |

这些失败均来自目标行为缺失或实现缺陷，不是依赖或测试环境故障。

## 4. 测试规格

| # | 保证 | 类型 | 结果 |
|---|---|---|---|
| 1 | 只扫描指定非终态、超过陈旧阈值的任务，批次 limit 生效 | unit | PASS |
| 2 | 多租户候选保持各自 tenant scope | unit | PASS |
| 3 | 租约被占用时跳过且不修改任务 | unit | PASS |
| 4 | 已完成准入与结果资产复用并收敛 completed | integration | PASS |
| 5 | 未知 query.execute 不重跑并失败关闭 | security | PASS |
| 6 | pending 审批保持等待，未知审批失败关闭 | integration | PASS |
| 7 | 准入 failed 使用稳定错误码收敛任务 | integration | PASS |
| 8 | safe checkpoint 只生成 `resume_safe_node` 计划 | unit | PASS |
| 9 | compensatable 工具只生成 `require_compensation` | security | PASS |
| 10 | 未知 checkpoint 版本失败关闭 | security | PASS |
| 11 | 单项异常和 fencing 接管不阻断剩余批次 | resilience | PASS |
| 12 | 重复扫描不重复写 Supervisor 事件 | integration | PASS |
| 13 | 报告与事件不含 SQL、参数、结果行或原始 checkpoint | security | PASS |
| 14 | MySQL 扫描有状态/时间/limit 边界，准入查询带 tenant | repository | PASS |
| 15 | 旧数据库 fencing token 不能提交恢复状态 | concurrency | PASS |
| 16 | runtime 默认关闭，且强制要求 Redis | configuration | PASS |
| 17 | Redis 获取异常只影响单项，不中断剩余候选 | resilience | PASS |
| 18 | Supervisor 事件必须由当前数据库 fencing token 原子提交 | concurrency | PASS |

## 5. GREEN 证据

```text
.venv\Scripts\python.exe -m pytest tests\test_recovery_supervisor.py tests\test_recovery_supervisor_runtime.py tests\test_task_recovery.py tests\test_agent_recovery_lease.py tests\test_query_request_admission.py tests\test_tool_replay_policy.py -q
61 passed

.venv\Scripts\python.exe -m pytest tests\test_mysql_migrate.py tests\test_api.py tests\test_query_application_service.py tests\test_conversation_service.py -q
60 passed

.venv\Scripts\python.exe -m pytest -q
1027 passed, 33 skipped, 1 warning in 44.60s

frontend\npm run build
80 modules transformed; build succeeded
```

补充验证：

- `compileall`、`pip check`、`git diff --check` 通过。
- V025 已应用本机 MySQL；`recovery_fencing_token BIGINT DEFAULT 0` 和 `idx_data_tasks_recovery(status,updated_at,recovery_fencing_token)` 已核对。
- 隔离 Redis 重启演练 `8/8` 通过，fencing token `1 → 2`，旧 holder 提交被拒绝。
- 专用临时租户的 MySQL + 隔离 Redis 冒烟：`scanned=1`、`reuse_existing_result`、任务 `completed`、Supervisor 事件 1 条；测试记录随后清理。
- CLI 未传 `--execute` 返回 `E_RECOVERY_EXECUTE_REQUIRED`；当前默认配置执行 `--execute` 返回 `E_RECOVERY_SUPERVISOR_DISABLED`，未扫描任务。

## 6. 设计边界与未覆盖项

- safe 节点当前只生成续跑计划。服务支持注入恢复执行器，但默认 runtime 没有通用 Graph 节点续跑实现，因此没有声称已自动恢复 Graph 执行。
- `export.cancel` 仍是补偿契约，不是自动补偿执行器。
- 默认没有常驻后台调度；一次性 CLI 必须显式 `--execute`，checkpoint 功能也默认关闭。
- Redis 演练是隔离单实例重启，不是 Sentinel/Cluster、网络分区或跨主机容灾证据。
- 当前环境未安装 `pytest-cov`，因此没有覆盖率百分比证据；不能用通过用例数量替代覆盖率。
- 项目工作区存在大量用户既有未提交修改，本轮未创建 Git checkpoint commit，也未清理或回滚这些修改；RED/GREEN 证据保存在本文档。
