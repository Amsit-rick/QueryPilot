# DUR-06 Branch Analysis TDD 记录

日期：2026-11-08

## 目标

从任意已完成历史轮次创建隔离分析分支。分支拥有独立任务生命周期，并重新执行当前账号的权限、Schema、SQL 安全、审批和审计；来源任务保持不变。

## RED

后端首轮新增 `tests/test_branch_analysis.py` 后运行得到：

```text
6 failed, 11 passed
```

失败覆盖请求字段互斥、任务血缘、分支上下文、Conversation 投影、分支族 API 和 V026 迁移缺失。

核心实现转绿后增加极端宽表预算用例，首次捕获：

```text
8049 > 1000
```

该失败证明来源 lineage 在宽表场景突破硬字符预算。修复为可裁剪 lineage，并显式记录 `lineage_truncated=true`。

前端 Playwright 首轮运行得到：

```text
1 failed
找不到按钮“从此轮创建分析分支”
```

证明工作台尚未接入分支交互，而不是通过修改测试绕过行为缺口。

最终复核补充 DUR-01 幂等重放交叉用例，首次失败为：

```text
KeyError: 'branch_origin'
```

根因是重复请求从任务结果资产重放时未重新附加任务血缘投影。修复后完成、审批、失败和运行中重放都会在分支任务上返回 `branch_origin`。

## GREEN

后端阶段：

```text
17 passed
25 passed
```

前端实现后，第一次运行发现两个按钮名称的包含匹配歧义；将提交按钮定位改为精确匹配后：

```text
1 passed
```

最终相关验证：

```text
76 passed
2 passed  # 多轮会话 + 分支分析 E2E
1056 passed, 33 skipped, 1 warning
```

前端生产构建、`compileall`、`pip check` 和 `git diff --check` 通过。390px 视口无横向滚动。

## MySQL 证据

`V026__add_task_analysis_branches.sql` 已应用到本机现有 MySQL 数据卷，迁移器输出 `applied V026`。只读核对确认 `data_tasks` 存在：

- `parent_task_id`
- `root_task_id`
- `branch_source_trace_id`
- `branch_label`
- `branch_depth`
- `idx_data_tasks_branch_parent`
- `idx_data_tasks_branch_root`

未删除、重建或清空数据卷。

## 覆盖场景

- 请求字段完整性和与追问模式互斥。
- 主任务与分支状态、步骤和结果资产隔离。
- 来源任务和分支任务双向审计证据。
- Conversation 分支来源投影。
- 分支族脱敏比较，不返回 SQL 或结果行。
- 只继承选定轮次之前的上下文。
- 无效、失败轮次拒绝。
- 跨账号来源拒绝。
- 最大分支深度和单任务子分支数量限制。
- 宽表来源上下文硬预算。
- 前端创建、跳转、比较、返回来源任务和移动端宽度。

## 未验收边界

本记录是本地开发和单实例 MySQL 证据，不代表生产多机并发、超大分支族容量、网络分区、真实企业权限变更过程或业务准确率验收。
