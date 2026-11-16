# MCP OAuth 与 Secret Governance TDD 证据

日期：2026-11-16

## 范围

本轮对应 `MS-09`：外部 MCP Server 支持 Bearer Secret 引用与 OAuth 2.0 Client Credentials；数据库、API、审计和任务证据
只保存认证方式、Secret 引用及非秘密元数据，不保存 Bearer Token、OAuth Access Token 或 Client Secret 值。

本轮复用现有 Redis MCP Runtime Guard 共享每日预算、并发和熔断状态，并为健康检查增加独立单飞键。没有连接真实企业 OAuth、
Vault/KMS、Redis Cluster 或多机网络环境，因此完成口径为本地代码和自动化合同通过，不是企业生产验收。

## 用户旅程

1. 管理员可以注册 Bearer 或 OAuth Client Credentials MCP Server，但只能提交 Secret 引用，不能提交明文 Token/Secret。
2. OAuth Access Token 在运行时按需获取、按租户/Server/凭据版本隔离缓存，并在过期前刷新。
3. 管理员轮换 Secret 引用时必须显式增加凭据版本、清除旧缓存并重置健康状态；普通 PATCH 不能绕过该流程。
4. 多实例使用 Redis 后端时共享预算、熔断和健康探针单飞状态；不同租户使用不同治理键。

## RED 证据

新增 `tests/test_mcp_credentials.py`、注册表/API/迁移用例后执行聚焦测试，首轮收集失败：

```text
ModuleNotFoundError: No module named 'app.services.mcp_credentials'
```

最小实现后首次聚焦结果为 `48 passed, 2 failed`，两项失败来自旧 MySQL 仓储测试仍使用 V019/V020 的列顺序；更新为
V028 合同后转绿。安全审查又产生两轮有效 RED：

- 普通 PATCH 可以修改 Secret 引用但不增加凭据版本，轮换旁路测试首轮 `DID NOT RAISE`。
- OAuth 响应中的对象型 `access_token` 和布尔型 `expires_in` 被 Python 隐式转换接受，新增两例首轮均 `DID NOT RAISE`。

实现专用轮换端点、禁止普通更新修改认证配置，并严格校验 Token 响应字段类型后转绿。

## GREEN 证据

| # | 保证 | 测试或命令 | 类型 | 结果 |
|---|---|---|---|---|
| 1 | 环境 Secret Provider 只解析受控引用，缺失和内联值失败关闭 | `tests/test_mcp_credentials.py` | 单元 | PASS |
| 2 | OAuth Token 按租户、Server 和凭据版本隔离缓存并提前刷新 | `tests/test_mcp_credentials.py` | 单元 | PASS |
| 3 | 上游异常与非法 Token 响应返回稳定错误且不泄露 Token/Secret | `tests/test_mcp_credentials.py` | 单元 | PASS |
| 4 | OAuth 配置、scope 和 Token URL 严格校验 | `tests/test_mcp_registry_service.py` | 单元 | PASS |
| 5 | 轮换增加版本、重置健康状态、写脱敏审计并按租户隔离 | `tests/test_mcp_registry_service.py` | 单元 | PASS |
| 6 | 普通 PATCH 不能绕过凭据版本轮换 | `tests/test_mcp_registry_service.py` | 单元 | PASS |
| 7 | API 拒绝 access_token/client_secret 字段且响应不返回 Secret | `tests/test_mcp_registry_api.py` | API 集成 | PASS |
| 8 | MySQL 迁移和仓储只保存引用、OAuth 非秘密元数据与版本 | `tests/test_mysql_migrate.py`, `tests/test_mcp_registry_repository.py` | 持久化 | PASS |
| 9 | 健康探针使用租户/Server 独立单飞键且不消耗每日调用预算 | `tests/test_mcp_invocation_service.py` | 集成 | PASS |
| 10 | MCP 管理页面可以配置 OAuth 并显式轮换引用 | `frontend: npm run build` | 前端构建 | PASS |

实际验证结果：

- OAuth/Secret/MCP 联合聚焦回归：`80 passed`。
- 后端最终全量：`1279 passed, 35 skipped, 1 warning`；warning 为既有 Starlette Cookie 测试弃用提示。
- 前端生产构建：`84 modules transformed`，构建成功。
- Python `compileall`、`pip check`、`git diff --check` 均通过；差异检查只报告工作区既有 LF/CRLF 提示。

## 安全合同

- 数据库继续使用 `auth_env_ref` 字段保存 `env://NAME` 或兼容的大写环境变量引用，不保存 Secret 值。
- OAuth 仅支持 `client_credentials`；Token URL 遵循与 MCP Endpoint 相同的 TLS、私网和基础设施地址限制。
- HTTP Token 请求禁用环境代理与重定向，响应上限 64KB；Access Token 只保存在进程内有界缓存中。
- 缓存键包含租户、Server ID 和 `credential_version`；显式轮换使旧缓存不可命中并执行主动清理。
- API、审计、DataTask 和 Agent Trace 不返回或记录 Bearer Token、Access Token、Client Secret 和 OAuth 响应正文。
- `MCP_RUNTIME_BACKEND=redis` 时，既有并发、每日预算和熔断 Lua 状态跨实例共享；健康检查使用独立单飞键。

## 已知边界

- 当前 Secret Provider 只实现环境变量引用；Vault、KMS、Kubernetes Secret、云 Secret Manager 仍需企业环境适配器。
- OAuth 仅实现 Client Credentials，不支持授权码、Device Code、动态客户端注册、刷新令牌或用户委托授权。
- Access Token 缓存是单进程内存，不把 Token 写入 Redis；多实例会分别向 OAuth Provider 获取 Token。
- 没有真实 OAuth Provider、mTLS、企业代理、Redis HA、网络分区或多机并发证据。
