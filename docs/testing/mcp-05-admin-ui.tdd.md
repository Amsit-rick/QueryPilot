# MCP-05 管理页面 TDD 证据

日期：2026-11-03

## 用户旅程

1. 管理员可以从工作区进入 MCP 管理页，查看当前租户的外部 Server 和运行状态。
2. 管理员可以登记、编辑、启停和删除 Server，表单只接收环境变量引用，不接收明文 Token 或 API Key。
3. 管理员可以执行健康检查，查看远端发现工具、延迟和结构化错误状态。
4. 管理员可以从“已发现且位于白名单”的工具中选择一个，提交 JSON 对象做显式只读诊断调用。
5. viewer/analyst 看不到入口，直接访问管理路由会返回工作台，后端仍独立执行 `manage` 权限校验。

## RED

新增 `frontend/e2e/mcp-admin.spec.js` 后首次运行被缺失的 Playwright Chromium 阻断，该结果不计为业务 RED。测试配置随后支持通过 `PLAYWRIGHT_EXECUTABLE_PATH` 复用本机 Chrome，不下载额外浏览器。

有效 RED：登录管理员后，Playwright 等待“`MCP 集成`”导航直到超时，因为路由、页面和 composable 尚不存在。失败位置为 `mcp-admin.spec.js` 的导航点击步骤。

## GREEN

- MCP 管理主流程与非管理员路由边界：`2 passed`
- MCP、模型运营、数据网关和语义层管理员回归：`5 passed`
- 后端 MCP 注册与运行时专项：`32 passed`
- Vite 生产构建：`75 modules transformed`
- 前端全量：`24 passed, 2 failed`；两项失败均使用本机真实默认账号，页面明确返回“用户名或密码错误”。MCP 两项和其他模拟隔离用例全部通过，未修改或绕过真实账号校验。

## 测试保证

| 保证 | 证据 |
|---|---|
| 创建请求只提交环境变量名和精确工具列表，不提交 `token`/`api_key` | `mcp-admin.spec.js` 创建请求断言 |
| 编辑、启停和删除均调用现有租户安全 API | `mcp-admin.spec.js` PATCH/DELETE 流程 |
| 健康检查后显示状态、延迟和发现工具 | `mcp-admin.spec.js` 健康检查流程 |
| 非 JSON 对象的诊断参数在浏览器端拒绝，未请求后端 | `mcp-admin.spec.js` 非法参数断言 |
| 仅健康、已启用且同时位于发现目录和白名单的工具可以调用 | 页面计算属性、composable 校验及停用断言 |
| analyst 无入口且不能停留在 `/admin/mcp` | `mcp-admin.spec.js` 非管理员用例 |
| 1440、768、390 三种宽度无页面横向溢出 | 同一 E2E 流程的 viewport 断言 |

## 浏览器检查

使用模拟 Server 和模拟凭据变量名生成 1440、768、390 三种宽度截图。人工检查确认：导航、摘要、配置表单、Server 状态、工具标签和诊断结果层级清晰；390px 页面切换为单列且无横向溢出。

## 已知边界

- 项目当前没有 Vitest/Vue Test Utils，前端证据以 Playwright 用户行为和生产构建为主，没有单独的组件覆盖率百分比。
- 自动检查未包含 axe-core；无横向溢出和语义标签通过不等于完整 WCAG 2.2 AA 验收。
- 两个依赖真实本机账号的登录 E2E 仍需用当前有效 `TEST_USERNAME/TEST_PASSWORD` 重新验收，不能计为产品通过。
- 页面展示调用结果仅用于管理员诊断，刷新后不持久化；结果仍可能包含远端返回的数据，生产运维应限制管理员和第三方 Server 范围。
- 工作区已有大量用户未提交改动，本批未创建 TDD 检查点提交，也未自动提交 Git。
