# SKILL-05 发布治理 TDD 证据

日期：2026-11-03

## 目标契约

- 新版本默认是 `draft`，不会改变当前运行版本。
- 灰度使用 `tenant_id + subject + skill_id + version` 的稳定哈希，同一身份重复请求结果一致。
- 灰度未命中时回退当前 `active` 或内置版本；租户之间不共享发布状态。
- 正式发布和回滚只保留一个当前 `active`，历史版本不删除。
- 首次发布前自动保存内置版本基线，确保能够回滚到发布前状态。
- 管理端只接受声明式 SkillDefinition，`allowed_tools` 继续受固定只读枚举约束。
- 管理 API 要求 `manage` 权限；普通 Skill API 和 MCP Resource 使用相同版本解析器。

## RED

首次运行：

```text
pytest tests/test_skill_service.py tests/test_skill_api.py tests/test_skill_repository.py
```

结果：测试收集失败，明确缺少 `SkillVersion`、`InMemorySkillRepository`、`MySQLSkillRepository` 和 `skill_runtime`。这证明版本治理能力尚未实现。

前端首次使用系统 Chrome 执行 `skill-release.spec.js` 时，页面流程运行到灰度状态，断言发现状态文本的实际无空格渲染与测试定位不一致。读取 Playwright accessibility snapshot 后确认功能已更新为 `canary_percent=25`，将定位器修正为状态元素语义断言，并把界面文案规范为“灰度 · 25%”。

## GREEN

- 后端新增 SkillVersion 域模型、Memory/MySQL 仓储、运行时仓储选择器、V021 建表迁移和 V022 约束迁移；V021 实际应用后保持不可变。
- 新增草稿、灰度、发布、回滚、预览和管理员目录 API。
- 新增 `/admin/skills` 管理页面，不提供代码、脚本、远程资源或系统提示词输入。
- API 与 MCP Resource 均通过同一 `SkillService` 解析租户版本。
- 本机 MySQL 已实际应用 V021/V022，并在隔离测试租户完成草稿、100% 灰度、发布、内置基线回滚和清理。

验证结果：

```text
SKILL-05/MCP 定向：23 passed
后端全量：768 passed, 11 skipped, 1 warning
Skill 页面 E2E：1 passed
前端全量：25 passed, 2 failed
前端生产构建：passed
```

两项前端全量失败均为依赖本机真实默认账号的登录用例未进入工作台；新增 Skill 管理页及其 mock 认证、路由和操作链全部通过。没有重置密码或绕过认证。

## 能力边界

`10/10` 只表示本专项本地开发与回归清单完成。生产环境仍需验证多实例并发发布、真实企业身份审批、集中审计采集、灾备恢复、容量阈值和第三方 MCP Server 行为；稳定灰度也不等于业务准确率已达标。
