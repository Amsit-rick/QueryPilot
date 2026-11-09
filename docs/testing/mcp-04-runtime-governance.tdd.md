# MCP-04 运行时治理 TDD 证据

日期：2026-11-03

## 范围

本批实现外部 Streamable HTTP MCP Server 的健康发现、受控只读调用、超时、并发、每日调用次数、熔断、网络目标策略、结果大小限制和脱敏审计。未开放 `query.execute`，未引入外部 stdio 命令。

## RED

在实现前先增加运行时治理与调用服务测试。首次收集失败，缺少以下模块：

```text
app.services.mcp_runtime_guard
app.services.mcp_invocation_service
```

这证明测试先于实现，并覆盖了尚不存在的运行时边界。

## GREEN

实现后逐步通过：

- MCP-04 注册、调用、治理、传输层、启动检查和审计专项：`84 passed`
- 后端全量：`758 passed, 11 skipped, 1 warning`
- `pip check`：无依赖冲突
- `compileall`：通过
- 合并门禁：`5/5`
- `git diff --check`：无空白错误，仅有 Windows CRLF 提示

## 测试保证

- 健康检查与调用前重新解析 DNS，并按地址类型执行失败关闭策略。
- 凭据只从环境变量引用解析，缺失时返回结构化错误。
- 工具必须同时位于精确 allowlist、由远端发现，并明确声明 `readOnlyHint=true` 且未声明 `destructiveHint=true`。
- 重复远端工具名、目录过大、分页过多、非法 JSON、非有限浮点、参数或结果过大均被拒绝。
- 工具发现、策略验证和实际调用在同一 MCP Session 中完成。
- 内存与 Redis 后端均覆盖并发租约、每日调用次数和熔断状态；Redis 不可用时失败关闭。
- 超时、上游错误、策略阻断和成功调用均产生不含参数、结果和 Token 的审计事件。
- MySQL V020 已实际应用，新增 11 个运行时治理与健康状态字段均存在。

## 真实协议验收

本机启动 Query Pilot Streamable HTTP MCP Server，随后运行：

```cmd
.venv\Scripts\python.exe scripts\mcp_runtime_acceptance.py --endpoint http://127.0.0.1:8011/mcp
```

结果：健康状态 `healthy`，发现 5 个只读工具，`skill.route` 返回 `resultType=complete`。验收脚本在 `finally` 中删除注册项；数据库复核剩余记录为 0。

## 已知边界

- `readOnlyHint` 是远端声明，不是对实现无副作用的密码学或运行时证明。
- DNS 在调用前复核，但应用层无法单独消除 DNS rebinding；生产需结合出口代理、防火墙和目标证书策略。
- 每日预算按调用次数统计，不代表 Token 或货币成本。
- 普通调用不自动重试，以避免潜在重复副作用。
- 本地 Redis 测试覆盖协议与失败关闭逻辑，Redis 集群高可用和容量阈值仍需部署环境压测。
