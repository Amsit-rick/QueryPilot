# MCP Agent Enrichment TDD 证据

日期：2026-11-16

## 范围

本轮对应 `MS-00～MS-06`：把受限只读外部 MCP 接入 Query Agent 主链路，增加确定性工具路由、结果清洗、
故障降级、任务证据和 Skill/MCP 联合契约评测。未实现任意工具调用、写工具、MCP 直接执行 SQL 或可执行 Skill 脚本。

## RED 1：服务契约不存在

先新增 `tests/test_mcp_agent_enrichment_service.py`，覆盖默认关闭、四工具白名单、普通查询不调用、租户/数据源角色边界、
问题摘要脱敏、注入清洗、结果大小/深度/根节点限制和超时降级。

执行：

```cmd
.venv\Scripts\python.exe -m pytest tests\test_mcp_agent_enrichment_service.py -q
```

首轮结果：收集阶段失败，`ModuleNotFoundError: app.services.mcp_agent_enrichment_service`。随后实现最小服务，GREEN 为
`14 passed`。

## RED 2：查询链路未注入 MCP

新增 API 用例，要求清洗后的 `mcp_enrichment` 到达 SQL 生成上下文，同时 DataTask 只保存脱敏调用证据。

首轮结果：失败于 `app.main` 不存在 `get_mcp_agent_enrichment_service`，证明管理面已有 MCP 调用并未进入查询应用服务。
完成运行时装配、依赖注入、Skill 后置 MCP 调用和 `resolve_semantics` 证据后，该用例通过。

## RED 3：缺少联合评测

先新增 `tests/test_mcp_skill_joint_evaluation.py`，首轮收集失败：
`ModuleNotFoundError: app.mcp_skill_joint_evaluation`。随后实现 12 条固定契约集和离线脚本，GREEN 为 `3 passed`。

## GREEN 回归

- 新增服务、API 集成和联合评测：`19 passed`。
- MCP/Skill 专项及关键查询回归：`96 passed`。
- 后端全量：`1231 passed, 35 skipped, 1 warning`；warning 为既有 Starlette Cookie 测试弃用提示。
- `compileall`、`pip check`、`git diff --check` 通过；差异检查只报告仓库既有 LF/CRLF 提示。
- 联合集明确分开 Skill 路由命中率、工具路由命中率和安全行为命中率；不把这些指标表述为 SQL 或业务准确率。

离线脚本首次实跑时错误复用了 MySQL Skill 仓储，在数据库未启动时失败；随后改为内置 Skill + 内存仓储，
证明该评测不依赖业务数据库。实际离线报告为 `12/12`，三个契约维度均为 `1.0`。

## 安全断言

- 默认 `MCP_AGENT_ENRICHMENT_ENABLED=false`，关闭时零外部调用。
- 只允许 `catalog.search`、`metric.lookup`、`lineage.get`、`policy.explain`。
- 模型不能选择 Server、Endpoint、Token 或未知工具。
- 外部结果作为 `untrusted_external_data` 嵌套数据，不是系统指令。
- 超限、超深、非法 JSON、交互错误和超时均显式降级，不跳过 SQL AST、权限、成本和审批门禁。
- Task/Trace 不保存完整参数、结果、Endpoint 或凭据。

## 未覆盖边界

本轮没有连接真实第三方 MCP Server，没有验证 OAuth、Vault、mTLS、多实例共享熔断、网络分区和容量；
没有把声明式 Skill 升级为可执行 LangGraph 子图。这些分别属于 `MS-07～MS-10`。
