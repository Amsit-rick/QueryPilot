# Executable Skill Graph TDD 证据

日期：2026-11-16

## 范围

本轮对应 `MS-07`：把少量高价值声明式 Skill 映射为平台拥有的固定工作流合同，并接入 bounded 与 LangGraph 两套运行时、
查询应用服务、模型预算门禁和 DataTask 回放证据。首批只支持 `sales-analysis` 与 `trend-analysis`。

本轮不执行租户发布的 `workflow_steps`，不支持任意 Python/Shell/网络脚本，不允许 Skill 注册未知工具、直接执行 SQL，
也不改变现有 SQL AST、RBAC、行级策略、成本、审批和 Data Gateway 权威边界。

## RED 1：工作流模块不存在

先新增 `tests/test_executable_skill_workflow.py`，覆盖平台固定步骤、租户文本不可覆盖、模糊问题澄清、显式 SQL 跳过生成、
趋势默认粒度、外部工具预算、未支持 Skill 以及 bounded/LangGraph 一致行为。

执行：

```cmd
.venv\Scripts\python.exe -m pytest tests\test_executable_skill_workflow.py -q
```

首轮在收集阶段失败：`ModuleNotFoundError: app.agent.skill_workflow`。完成最小模板、图状态和前置节点后，基础图用例转绿。

## RED 2：预算只有声明，没有执行门禁

继续增加模型调用、Token 和费用预算测试，首轮在收集阶段失败：
`ImportError: cannot import name 'SkillWorkflowBudgetError'`。随后实现稳定、不可重试的预算错误和 fail-closed 合同校验，
并在 SQL 生成与语义 Critic 的每次模型调用前执行门禁。

API 级测试进一步把 Token 上限压到 1，要求模型函数绝不能被调用，同时任务中必须保存
`E_SKILL_TOKEN_BUDGET` 失败证据。该测试证明预算不是只展示在合同中的元数据。

## RED 3：查询链路没有工作流回放证据

新增 API 用例，要求成功响应返回模板 ID/版本/状态，DataTask 在 `resolve_semantics` 后保存独立 `skill_workflow` 步骤；
模糊问题必须在生成 SQL 前停止，并可通过任务详情回放澄清原因。接入前响应和任务步骤均缺少该合同，完成依赖注入、
Schema 召回上下文回写、前置节点、终态收口和响应摘要后转绿。

## GREEN 回归

- Executable Skill 单元与双运行时测试：`16 passed`。
- 新增工作流/API 聚焦回归：`19 passed`。
- Agent 图、反思、API 与请求幂等联合回归：`128 passed`。
- 后端最终全量：`1250 passed, 35 skipped, 1 warning`；warning 为既有 Starlette Cookie 测试弃用提示。
- Python `compileall`、`pip check` 和 `git diff --check` 通过；差异检查只报告仓库既有 LF/CRLF 提示。

预算边界补强后的首次全量中，1500 表 Schema 召回时间型门槛出现一次瞬时失败；单独复跑为
`p95=114.046ms`，明显低于 `1000ms` 门槛，第二次完整全量全部通过。未放宽性能门槛，也未把首次失败隐藏为成功。

## 固定合同

步骤顺序：

```text
clarify -> retrieve_metric -> retrieve_schema -> validate_grain
-> generate_sql -> validate_sql -> execute -> verify_result -> explain
```

允许工具固定为 `metric.lookup`、`schema.inspect`、`query.generate`、`query.validate`、`query.execute`、
`result.verify`、`result.explain`。默认预算为最多 2 次外部工具调用、3 次模型调用、50000 Token 和 1 美元估算费用。

租户发布版本只能决定是否命中对应 Skill 及其声明版本，不能修改模板步骤、允许工具和预算。当前任务证据不会保存租户
`workflow_steps` 文本，也不会保存问题原文、Prompt、SQL、参数、MCP 原始结果、Token、Endpoint 或密钥。

## 未覆盖边界

- 仅两个平台审核 Skill 进入固定模板；其余 Skill 仍为声明式上下文。
- 尚未实现受限 `SKILL.md` 导入、远程 OAuth/Secret 引用、多实例共享预算和客户端兼容矩阵，对应 `MS-08～MS-10`。
- 本轮是本地代码与自动化验收，不代表真实企业业务准确率、第三方 MCP 行为、生产容量、费用阈值或多机容灾验收。
