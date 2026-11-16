# MS-10 MCP Client Compatibility Matrix TDD 证据

日期：2026-11-16

## 来源计划

来源为 `项目改造计划书.md` 的 `MS-10 Client Compatibility Matrix`。计划内容只作为需求输入；本轮采用项目现有 pytest、官方 Python MCP SDK 和本机回环传输进行验证，没有执行远程安装、修改用户客户端配置或打印凭据。

## 用户旅程

1. 作为维护者，我希望用固定 MCP SDK 版本复现 stdio 与 Streamable HTTP 连接，避免凭文档推断兼容性。
2. 作为客户端使用者，我希望 Tools、Resource Template、资源读取和工具调用在两种传输下契约一致。
3. 作为安全负责人，我希望非法参数和缺少凭据返回稳定、脱敏错误码。
4. 作为发布负责人，我希望未实际执行的 Claude Code/Desktop、Cursor、Codex 明确标记 `not_tested`。
5. 作为运维人员，我希望客户端取消后传输可以正常关闭，不遗留 stdio 子进程或 HTTP Server。

## RED 证据

| 阶段 | 命令 | 失败结果 | 证明 |
| --- | --- | --- | --- |
| 缺少实现 | `.venv\Scripts\python.exe -m pytest tests\test_mcp_client_compatibility.py -q` | `ModuleNotFoundError: No module named 'app.mcp_client_compatibility'` | 项目没有兼容矩阵模型和探针 |
| 缺少实跑矩阵 | 同上 | `FileNotFoundError: data/mcp-client-compatibility.json`，其余 4 项通过 | 不能只写生成器，必须保存真实执行结果 |
| 错误码与空结果边界 | `.venv\Scripts\python.exe -m pytest tests\test_mcp_client_compatibility.py tests\test_mcp_server.py -q` | `3 failed, 12 passed` | 空检查集被误判 verified；MCP Server 把业务和认证错误隐藏成通用 `Error executing tool` |
| Windows 真实 stdio | `.venv\Scripts\python.exe scripts\verify_mcp_client_compatibility.py --transport all ...` | `io.UnsupportedOperation: fileno` | Windows 子进程要求原生 stderr 文件句柄，内存 StringIO 不可用 |
| 报告可信度审查 | `.venv\Scripts\python.exe -m pytest tests\test_mcp_client_compatibility.py -q` | `2 failed, 6 passed` | 单传输运行遗漏未测传输；探针异常退出后旧通过 JSON 未被覆盖 |
| 客户端目录完整性 | 单测产品客户端集合 | 缺少 `claude-code` | Claude Code 与 Claude Desktop 不是同一客户端，不能合并记录 |

## GREEN 证据

| 行为 | 验证命令 | 结果 | 保证 |
| --- | --- | --- | --- |
| 矩阵模型、取消、脚本与 MCP 契约 | `.venv\Scripts\python.exe -m pytest tests\test_mcp_client_compatibility.py tests\test_mcp_server.py tests\test_mcp_transport.py -q` | `20 passed` | 空检查失败关闭；失败覆盖陈旧报告；未测传输显式记录；既有传输适配不回归 |
| 双传输真实探针 | `.venv\Scripts\python.exe scripts\verify_mcp_client_compatibility.py --transport all --output data\mcp-client-compatibility.json` | `verified=2, failed=0, not_tested=4` | 官方 SDK 2.3.0 实际跑通 stdio 与回环 Streamable HTTP；产品客户端未冒充通过 |
| 后端完整回归 | `.venv\Scripts\python.exe -m pytest -q` | `1289 passed, 35 skipped, 1 warning` | 新增错误契约和探针未破坏项目其他后端能力 |
| 静态与构建门禁 | `compileall`、`pip check`、前端 `npm run build`、`git diff --check` | 全部退出码 0；前端 84 modules transformed | Python 可编译、依赖一致、前端可构建且无空白错误 |

## 测试规格

| # | 保证 | 测试或证据 | 类型 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | 只有实际执行的 SDK/传输可进入 verified | `test_matrix_only_marks_executed_official_sdk_transports_as_verified` | unit | PASS |
| 2 | 任一失败或空检查集不能被误报 verified | `test_matrix_downgrades...`、`test_matrix_never_marks...` | unit | PASS |
| 3 | 客户端取消可传播到待处理请求 | `test_cancellation_probe_observes_cancelled_client_request` | unit | PASS |
| 4 | 兼容脚本可直接执行并暴露明确 CLI | `test_compatibility_script_is_directly_executable` | integration | PASS |
| 5 | 机器可读矩阵固定 SDK 版本且不冒充产品客户端 | `test_checked_in_matrix_is_machine_readable...` | contract | PASS |
| 6 | MCP 业务错误保留稳定码 | `test_mcp_tool_returns_stable_application_error_code` | integration | PASS |
| 7 | MCP 认证错误保留稳定码 | `test_mcp_tool_returns_stable_authentication_error_code` | integration | PASS |
| 8 | stdio 与 HTTP 的 Tools/Resource/调用/错误/认证/取消实际通过 | `data/mcp-client-compatibility.json` | protocol acceptance | PASS |
| 9 | 单传输运行必须显式标记另一传输未测试 | `test_matrix_marks_unexecuted_official_transport_as_not_tested` | unit | PASS |
| 10 | 探针失败必须原子替换陈旧通过报告且不泄露路径/异常内容 | `test_compatibility_script_replaces_stale_success_report_when_probe_fails` | integration | PASS |

## 已知缺口

- 本机检测到 Claude Code `2.1.325` 和 Codex CLI `0.167.0-alpha.4.1`，但没有修改用户配置或发起模型会话完成全链路验收；Claude Code/Desktop、Cursor、Codex 均为 `not_tested`。
- 没有验证真实长查询的服务端业务取消、TLS/mTLS、反向代理、企业 OAuth、网络分区和容量。
- 没有覆盖 MCP Prompt、Sampling、Elicitation、Roots 或任意第三方 Server 行为。
- 本轮不创建 TDD checkpoint commit，因为用户要求不自行 commit/push；RED/GREEN 证据保存在本文件。
- 完整回归的 35 个 skip 是既有可选/外部环境测试，不计为通过；唯一 warning 是既有 Starlette per-request Cookie 弃用提示。
