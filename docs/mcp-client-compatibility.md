# MCP 客户端兼容矩阵

更新时间：2026-11-16

## 验收口径

本矩阵只把实际执行过的客户端版本和传输标记为 `verified`。客户端名称出现在表中不等于已经兼容；没有固定版本、没有可复现命令或没有实际连接证据时，一律标记为 `not_tested`。

当前机器实际执行的是项目依赖锁定的官方 Python MCP SDK `2.3.0`，协商协议版本为 `2025-11-25`。验证脚本会临时生成只存在于进程环境中的 API Key，分别启动 stdio 和回环 Streamable HTTP Server，不把 Key 写入报告、日志或项目文件。

机器可读结果位于 `data/mcp-client-compatibility.json`。

## 当前矩阵

| 客户端 | 固定版本 | 传输 | 状态 | 实际验证 |
| --- | --- | --- | --- | --- |
| Official Python MCP SDK | 2.3.0 | stdio | verified | 初始化、Tools、Resource Template、资源读取、工具调用、稳定错误码、缺少凭据、客户端取消与进程关闭 |
| Official Python MCP SDK | 2.3.0 | Streamable HTTP | verified | 初始化、Tools、Resource Template、资源读取、工具调用、稳定错误码、缺少凭据、客户端取消与传输关闭 |
| Claude Code | 2.1.325（本机检测） | stdio / Streamable HTTP | not_tested | 只检测到版本，没有修改用户配置或发起模型会话进行全链路验收 |
| Claude Desktop | 未固定 | stdio / Streamable HTTP | not_tested | 本机未安装并执行，不声明兼容 |
| Cursor | 未固定 | stdio / Streamable HTTP | not_tested | 本机未执行固定版本，不声明兼容 |
| Codex CLI / App | 0.167.0-alpha.4.1（本机检测） | stdio / Streamable HTTP | not_tested | 只检测到版本；未修改用户配置，当前 App 会话不作为协议证据 |

两种已验证传输均确认：

- 精确列出五个只读 Tool，包含 `readOnlyHint=true` 与 `destructiveHint=false`。
- 列出并读取 `querypilot://skills/catalog/{tenant_id}` Resource Template。
- `skill.route` 返回确定性的应收分析 Skill。
- 非法 `query.plan` 返回稳定错误码 `E_INTEGRATION_QUESTION`。
- 缺少身份凭据返回稳定错误码 `E_MCP_AUTH`，不回显 Secret。
- 客户端取消能够传播到待处理请求任务，随后传输上下文可正常关闭。

## 复现命令

在项目根目录使用 Windows CMD：

```cmd
.venv\Scripts\python.exe scripts\verify_mcp_client_compatibility.py --transport all --output data\mcp-client-compatibility.json
```

只验证单一传输：

```cmd
.venv\Scripts\python.exe scripts\verify_mcp_client_compatibility.py --transport stdio --output artifacts\mcp-stdio-compatibility.json
.venv\Scripts\python.exe scripts\verify_mcp_client_compatibility.py --transport streamable-http --output artifacts\mcp-http-compatibility.json
```

脚本仅允许绑定 `127.0.0.1`、`localhost` 或 `::1`；HTTP 端口默认为临时空闲端口。任一已执行检查失败时退出码为非零。

## 能力边界

- `verified` 表示本机协议级自动化通过，不表示 Claude Code/Desktop、Cursor、Codex 或其他产品客户端已通过。
- 单独执行一种传输时，另一种官方 SDK 传输会明确记录为 `not_tested`；探针异常会用失败报告原子替换旧结果，避免保留陈旧通过矩阵。
- 取消检查证明客户端请求任务可取消并能清理传输；没有用真实长 SQL 证明服务端业务线程已经被强制中断。
- Streamable HTTP 只在回环 HTTP 验证，未覆盖 TLS、反向代理、mTLS、企业 OAuth、网络分区、代理兼容或公网暴露。
- Resource 验证只覆盖当前 Skill Catalog Template，不代表完整 MCP Resource、Prompt、Sampling、Elicitation 或 Roots 能力。
- 该矩阵不替代第三方客户端升级后的回归；客户端版本变化后必须重新执行并保留新证据。
