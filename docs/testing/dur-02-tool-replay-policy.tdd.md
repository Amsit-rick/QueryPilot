# DUR-02 工具重放策略 TDD 证据

日期：2026-11-07

## 1. 目标

- 恢复流程必须先识别工具副作用，不能把所有失败都当成可重试。
- Schema 等确定性只读工具可以安全重放。
- SQL 执行和审批创建即使技术上只读，也不能在结果未知时自动重放。
- 导出入队必须先完成补偿，再允许重新入队。
- 外部 MCP 工具只有同时声明只读、非破坏和幂等时才能标记为安全。
- 未注册工具默认 `unsafe`，失败关闭。

## 2. RED 证据

首次命令：

```cmd
.venv\Scripts\python.exe -m pytest tests\test_tool_replay_policy.py -q
```

首次结果：测试收集失败，`ModuleNotFoundError: No module named 'app.domain.tool_replay'`，证明策略领域与服务尚不存在。

集成阶段还捕获两个真实问题：

1. MCP 审计最初不接受重放策略字段，联合测试出现 16 个失败；随后扩展审计白名单并重新通过。
2. 模拟 MCP Transport 不调用工具目录校验回调时，代码最初出现 `IndexError`；新增失败关闭测试后改为稳定错误 `E_MCP_TOOL_CATALOG_INVALID`。

## 3. 策略矩阵

| 工具 | 分类 | 恢复行为 |
|---|---|---|
| `schema.inspect` | safe | 允许重放 |
| `query.validate` / `query.explain` | safe | 允许重放 |
| `query.execute` | unsafe | 结果未知时阻断 |
| `approval.create` | unsafe | 状态未知时阻断 |
| `export.enqueue` | compensatable | 先要求 `export.cancel` 补偿证据 |
| `result.materialize` | safe | 依赖既有资产幂等键重放 |
| `mcp.invoke:<tool>` | dynamic | 只读、非破坏且幂等才是 safe |
| 未注册工具 | unsafe | 默认阻断 |

## 4. GREEN 证据

```text
.venv\Scripts\python.exe -m pytest tests\test_tool_replay_policy.py -q
7 passed

.venv\Scripts\python.exe -m pytest tests\test_tool_replay_policy.py tests\test_task_recovery.py tests\test_mcp_invocation_service.py tests\test_audit.py tests\test_api.py tests\test_query_request_admission.py -q
105 passed in 14.56s

.venv\Scripts\python.exe -m pytest -q
1004 passed, 33 skipped, 1 warning in 44.70s

frontend\npm run build
80 modules transformed; built successfully
```

Python `compileall`、`pip check` 和差异空白检查通过。

## 5. 可审计性

- Query Task 的 `understand_question`、`query`、`approval` 和 `materialize_artifacts` 步骤保存版本化 `tool_replay` 元数据。
- TaskRecovery 事件保存 `replay_action`、`blocked_tool` 和策略版本。
- MCP 成功调用的脱敏审计保存重放分类和策略版本，不保存参数、结果正文或凭据。
- 导出创建和状态响应返回静态重放策略，便于后续 Supervisor 做补偿门禁。

## 6. 边界

- 本轮只实现策略、判定、持久化标记和失败关闭，不自动执行恢复动作。
- `export.cancel` 当前是补偿动作契约，不是已经交付的自动取消执行器；没有补偿完成证据时只能阻断重放。
- DUR-03 才会实现扫描非终态任务、获取租约、读取 checkpoint、执行安全工具或请求人工处理的 Supervisor。
- 当前环境未安装 `pytest-cov`，未声明覆盖率百分比。

