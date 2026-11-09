# DataTask 恢复运行手册

更新时间：2026-11-07

## 恢复原则

恢复操作只根据持久化的 Task、Step、Artifact、审批、请求准入和版本化 checkpoint 收敛任务，不自动重新执行 SQL，也不自动再次写入结果资产。Supervisor 以有界批次运行，每个候选独立获取租约、认领数据库 fencing token 并写入幂等恢复事件；同一证据重复扫描不会重复执行恢复动作。

## 工具重放策略

所有恢复动作必须先经过 `tool-replay-v1` 策略判定：

| 分类 | 行为 |
|---|---|
| `safe` | Supervisor 可以生成受 fencing 约束的续跑计划；只有显式注入恢复执行器后才实际重放 |
| `unsafe` | 结果未知时禁止自动重放，任务失败关闭或进入人工处理 |
| `compensatable` | 必须先取得补偿完成证据，才能重新执行 |

当前 `schema.inspect`、SQL 校验/EXPLAIN 和幂等结果物化为 `safe`；`query.execute`、`approval.create` 为 `unsafe`；`export.enqueue` 为 `compensatable`。MCP 根据远端工具的只读、破坏性和幂等标注动态判定，缺少任一安全证据时按 `unsafe` 处理。未注册工具默认失败关闭。

Task Step、恢复事件和 MCP 审计会记录策略版本与判定。DUR-03 已实现 Supervisor 判定和安全终态收敛，但默认 runtime 不自动执行 Graph 节点重放；`export.cancel` 仍只是补偿契约，没有补偿完成证据时保持阻断。

Agent 运行时在各节点完成后保存版本化进度快照；快照带 `schema_version`、任务/租户、执行 ID、节点游标和 fencing token，不保存参数值、原始结果行或模型原始响应。未知未来版本 fail-closed。若快照写入失败，当前调用以 `checkpoint_failed` 结束并保留已生成 SQL，禁止因为检查点故障再次执行查询。

启用 checkpoint 后，每个任务先获取 Redis 恢复租约。租约使用原子 Lua 脚本分配单调 fencing token，提交 checkpoint 和释放租约时校验持有者、token 和过期状态。V025 还为 `data_tasks` 增加数据库 fencing token：Supervisor 必须先用更大的 token 认领任务，状态更新只允许当前 token 提交。Redis 约束 checkpoint，MySQL 条件更新约束任务终态；旧实例不能覆盖新实例。

## 状态处理

| 中断状态 | 持久化证据 | 恢复结果 |
|---|---|---|
| `running` / `verifying` | 已存在带幂等键的结果资产 | 转为 `completed` |
| `running` / `verifying` | 查询步骤完成但无结果资产 | 转为 `failed`，禁止自动重跑 SQL |
| `planning` / `running` | 无完成查询和结果资产 | 转为 `failed`，由用户显式重试 |
| `approval_required` | 审批仍为 pending | 保持等待审批 |
| `approval_required` | 审批已批准/拒绝但任务结果不确定 | 转为 `failed`，人工核对审批审计后重试 |
| `planning` / `running` | 准入记录为 `failed` | 转为 `failed`，保留准入稳定错误码 |
| `planning` / `running` | checkpoint 指向 safe 工具 | 记录 `resume_safe_node` 计划，默认不声称已恢复执行 |
| 任意可恢复状态 | checkpoint 版本未知或身份不一致 | fail-closed，拒绝载入原始 payload |
| `completed` / `failed` / `cancelled` | 任意 | 保持原终态 |

## 操作步骤

1. 确认已执行 MySQL 迁移 V025，并配置可用 Redis。默认 `AGENT_CHECKPOINT_ENABLED=false`，不会自动运行 Supervisor。
2. 在 CMD 中显式启用本次进程并执行一次有界扫描：

```cmd
cd /d D:\QueryPilot
set AGENT_CHECKPOINT_ENABLED=true
set REDIS_URL=redis://127.0.0.1:6379/0
.venv\Scripts\python.exe scripts\recovery_supervisor.py --execute --limit 50 --stale-after-seconds 120
```

3. 核对输出的 `scanned/recovered/skipped/failed`，以及每项的 action、稳定错误码、fencing token、checkpoint 节点和准入状态。输出不会包含 SQL、参数或结果行。
4. 检查 `recovery_supervisor` 和 `task_recovery` 事件。不确定 `query.execute` 必须为 `fail_uncertain_side_effect`，不得出现自动查询重放。
5. `resume_safe_node` 只代表安全续跑计划；当前默认运行时没有通用 Graph 节点恢复执行器。`require_compensation` 需要人工或后续补偿执行器确认 `export.cancel` 完成后才能重试。

## 故障边界

- 检查点失败：检查点不是结果提交证明。即使查询步骤已完成，无结果资产也不得自动重跑或标记完成。
- 资产写入失败：没有持久化资产时无法证明结果可恢复，不调用资产写入回调，也不重复查询。
- 服务重启：DUR-03 已提供一次性有界扫描器，但没有常驻 daemon、Cron 或自动启动钩子；是否调度由部署环境显式决定。
- 并发节点恢复：Redis 租约、checkpoint fencing 和 MySQL 状态 fencing 已阻止已知旧 token 提交。尚未完成 Sentinel/Cluster、网络分区和跨主机双实例故障演练，不能宣称生产级灾备已验收。
- 安全节点续跑：当前只生成计划；没有通用 Graph 节点恢复执行器，因此不能把 `resume_safe_node` 解释为任务已经继续执行。
- 补偿：`export.cancel` 仍未实现自动执行；待补偿任务不会被自动重新入队。

## 回滚

停止调用 `scripts/recovery_supervisor.py` 即可停止扫描；系统默认不会自动运行。已经写入的 `task_recovery`、`recovery_supervisor` 和 fencing token 应保留。不要把已被恢复为失败的任务直接改回运行态，应通过现有显式任务重试流程创建新的执行尝试；不要回退数据库 fencing token。
