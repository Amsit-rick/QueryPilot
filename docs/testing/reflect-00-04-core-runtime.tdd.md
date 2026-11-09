# REFLECT-00～04 核心运行时 TDD 证据

## 范围

来源计划：`docs/reflection-mechanism-roadmap.md`。

本批完成现状回归锁定、结构化反思契约、共享 ReflectionPolicy、分类型预算、LangGraph 显式 reflect 节点，以及 bounded/LangGraph 行为对齐。不包含结果业务语义验证、LLM Critic、DataTask 前端时间线或离线灰度评测。

## 用户旅程

1. 模型生成的 SQL 发生可修复语法错误时，系统携带稳定错误码重新生成，并限制次数。
2. Schema 缺失时，系统只在当前授权范围内重新召回语义上下文，再重新生成。
3. 数据库瞬时错误只重试相同 SQL，但每次执行前都重新经过校验、审批和成本门禁。
4. 写 SQL、Prompt 注入、权限拒绝和用户显式 SQL 失败时，系统不得通过模型重试绕过。
5. bounded 与 LangGraph 对相同错误给出相同动作、预算和终态错误契约。
6. checkpoint、反思历史、API 和 DataTask 不保存原始模型载荷、参数值或凭据文本。

## RED

第一次 RED：

```text
.venv\Scripts\python.exe -m pytest tests/test_agent_reflection.py -q
ERROR tests/test_agent_reflection.py
ModuleNotFoundError: No module named 'app.agent.reflection'
```

该失败证明反思领域模块和契约尚不存在。

第二次 RED 在独立审查后补充，结果为：

```text
9 failed, 15 passed
```

失败覆盖：成本服务失败被误标审批、Bearer/URI/多行指令脱敏不足、LangGraph 执行重试未重走门禁、LangGraph 生成异常绕过 reflect、`PermissionError` 被重试、bounded checkpoint 失败状态不一致、恢复状态可抬高预算上限。

## GREEN

核心实现：

- 新增 `app/agent/reflection.py`：`ReflectionResult`、失败类型、动作、预算、策略、安全公共错误和统一 `AgentTerminalError`。
- `QueryAgentState` 增加反思、历史、预算和稳定错误码；checkpoint 不保存参数值和原始异常。
- bounded 与 LangGraph 共用 ReflectionPolicy、修复提示、预算恢复和终态错误契约。
- LangGraph 新增显式 `reflect`、`retrieve_semantics` 路由；生成、校验、前置门禁、执行和结果验证失败均进入受控决策。
- 相同 SQL 的执行重试在两种运行时都重新经过 validate 和 pre_execute。
- 恢复状态只恢复已消费计数，所有最大次数由当前服务端配置覆盖。
- 查询应用服务显式传入 Schema 扩召回、执行重试和总反思步数配置。

最终专项与相关回归：

```text
.venv\Scripts\python.exe -m pytest tests/test_agent_reflection.py tests/test_agent_graph.py tests/test_agent_contracts.py tests/test_quality_feedback.py tests/test_query_application_service.py tests/test_api.py tests/test_query_streaming.py tests/test_query_cost_and_relationships.py -q
137 passed
```

全量后端：

```text
.venv\Scripts\python.exe -m compileall -q app tests
.venv\Scripts\python.exe -m pytest -q
831 passed, 32 skipped, 1 warning in 38.96s
```

32 个 skipped 仍主要是未配置的 Oracle、SQL Server、ClickHouse 真实实例探针，不计为通过。warning 为既有 Starlette TestClient cookie 弃用提示。

## 测试保证

| 保证 | 主要测试 | 类型 | 结果 |
|---|---|---|---|
| 反思契约拒绝冲突动作 | `test_reflection_result_rejects_conflicting_acceptance_and_action` | 单元 | PASS |
| 写 SQL、权限和显式 SQL 不进入修复循环 | `test_security_failure_is_not_repaired`、`test_permission_error_is_never_retried`、`test_caller_sql_failure_is_never_replaced_by_generated_sql` | 单元/运行时 | PASS |
| 两种运行时使用结构化错误反馈 | `test_runtime_passes_structured_validation_feedback_to_regeneration` | 运行时 | PASS |
| Schema 错误先扩召回再生成 | `test_schema_failure_expands_semantics_before_regeneration` | 运行时 | PASS |
| 瞬时执行重试相同 SQL 且重走门禁 | `test_retryable_execution_error_retries_same_sql_without_regeneration` | 安全回归 | PASS |
| 生成异常进入显式反思 | `test_generation_failure_enters_reflection_and_can_recover` | 运行时 | PASS |
| checkpoint 失败立即停止 | `test_reflection_checkpoint_failure_stops_without_more_generation` | 可靠性 | PASS |
| 恢复状态不能抬高当前预算 | `test_restored_budget_cannot_raise_current_runtime_limits` | 安全回归 | PASS |
| 终态异常在两种运行时一致 | `test_terminal_error_contract_is_identical_when_execution_budget_is_zero` | 契约 | PASS |
| 非法模型载荷不进入 API、任务或反思历史 | `test_terminal_error_contract_hides_invalid_model_payload`、`test_invalid_model_payload_never_leaks_into_api_or_task_history` | 集成/安全 | PASS |

## 独立审查

- 代码审查首次发现 6 项问题，全部修复；最终复核无阻断项，复核结果 `86 passed`。
- 安全审查首次发现 4 项问题及 1 项终态泄漏风险，全部修复；最终复核无安全阻断项，复核结果 `30 passed`。

## 覆盖率与限制

尝试执行：

```text
.venv\Scripts\python.exe -m pytest tests/test_agent_reflection.py --cov=app.agent.reflection --cov-report=term-missing -q
```

当前环境未安装 `pytest-cov`，pytest 报告 `unrecognized arguments: --cov`，因此本批不能声明已取得覆盖率百分比。专项测试、相关回归和全量测试均已实际执行，但不能替代覆盖率报告。

本批未实现 `REFLECT-05～08`：业务结果质量验证、受控语义 Critic、DataTask/前端反思展示、离线评测与 shadow/enforced 灰度仍待后续开发。

## Git 证据

任务开始前共享工作区已有大量跨模块未提交修改，且本批修改文件与既有改动重叠。为避免把用户现存修改混入 checkpoint commit，本批未自动提交；RED/GREEN、审查和全量回归证据保存在本文档。
