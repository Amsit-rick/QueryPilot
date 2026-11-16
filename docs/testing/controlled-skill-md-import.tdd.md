# Controlled SKILL.md Import TDD 证据

日期：2026-11-16

## 范围

本轮对应 `MS-08`：管理员可以预览并确认导入受限声明式 `SKILL.md`，确认后只复用现有发布治理链创建
`draft` 版本。Markdown 正文不导入、不保存、不执行；系统不联网下载远程来源，不执行 Python、Shell、安装命令或未知资源。

SHA-256 用于证明预览与确认期间内容未变化，只是完整性声明，不验证发布者身份，也不是代码签名或供应链信任证明。

## 用户旅程

1. 管理员导入声明式 `SKILL.md` 前可以查看结构化定义、来源、完整性状态和与当前 Skill 的字段差异。
2. 管理员确认的内容、租户、身份或当前基线发生变化时，系统拒绝使用旧预览。
3. 不支持字段、Prompt 注入语义、超限内容和策略权限扩张必须失败关闭。
4. 确认导入只创建草稿，后续仍走原有灰度、评测、发布和回滚流程。

## RED 证据

先新增 `tests/test_skill_import_service.py` 与 `tests/test_skill_api.py` 的导入用例，再运行：

```cmd
.venv\Scripts\python.exe -m pytest tests\test_skill_import_service.py tests\test_skill_api.py -q
```

首轮在收集阶段失败：

```text
ModuleNotFoundError: No module named 'app.services.skill_import_service'
```

实现服务后，64KB 参数化用例一度因 pytest 把完整参数写入测试目录名，在 Windows 触发路径过长；该问题通过给参数化用例设置
稳定短 ID 修复，没有放宽生产内容限制。

定向审查又发现 PyYAML 默认允许重复键。新增重复 `id` 用例后，首轮实际得到 `E_SKILL_IMPORT_POLICY`，证明前值被静默覆盖；
随后加入拒绝重复映射键的专用 SafeLoader，测试转为稳定的 `E_SKILL_IMPORT_YAML_FEATURE`。

## GREEN 证据

| # | 保证 | 测试或命令 | 类型 | 结果 |
|---|---|---|---|---|
| 1 | Front Matter 严格解析，Markdown 正文不进入结果 | `tests/test_skill_import_service.py` | 单元 | PASS |
| 2 | 未知/重复字段、Prompt 注入、超过 64KB、摘要不匹配失败关闭 | `tests/test_skill_import_service.py` | 单元 | PASS |
| 3 | 已有 Skill 的工具、角色、只读和优先级策略不能通过导入修改 | `tests/test_skill_import_service.py` | 单元 | PASS |
| 4 | 确认令牌绑定内容、来源、租户、管理员和当前 Skill 基线，并有 10 分钟有效期 | `tests/test_skill_import_service.py` | 单元 | PASS |
| 5 | 管理员确认后只创建 draft，不改变 active 版本 | `tests/test_skill_api.py` | API 集成 | PASS |
| 6 | 审计保存事件与摘要，不保存 Markdown 正文 | `tests/test_skill_api.py` | API 集成 | PASS |
| 7 | 非管理员不能访问导入预览和确认端点 | `tests/test_skill_api.py` | API 集成 | PASS |
| 8 | 管理页面导入组件可以完成生产构建 | `frontend: npm run build` | 前端构建 | PASS |

实际验证结果：

- 导入与原发布链聚焦回归：`37 passed`。
- Skill/MCP 联合回归：`77 passed`。
- 后端全量：`1263 passed, 35 skipped, 1 warning`；warning 为既有 Starlette Cookie 测试弃用提示。
- 前端生产构建：`84 modules transformed`，构建成功。
- Python `compileall`、`pip check`、`git diff --check` 通过；差异检查只报告工作区既有 LF/CRLF 提示。

## 安全合同

- Front Matter 只接受 `query-pilot-skill-import/v1` 和平台 `SkillDefinition` 的已知字段。
- 拒绝 YAML Anchor、Alias、Tag、Merge Key、重复键和未知字段。
- 本地来源只接受文件名；远程来源只接受不带凭据、查询参数和片段的 HTTPS 元数据，后端不会访问该 URL。
- 预览确认令牌使用 HMAC，绑定内容摘要、来源摘要、定义摘要、租户、管理员和当前基线摘要。
- 已有 Skill 的 `allowed_tools`、`allowed_roles`、`read_only`、`priority` 不能通过导入改变。
- 确认端点复用 `SkillService.create_version`，只创建 `draft`；发布、灰度、回滚和评测仍由原治理链负责。

## 已知边界

- 没有实现可信发布者证书、Sigstore、PGP 或企业 CA 数字签名。
- 没有实现远程仓库自动下载、依赖安装、脚本执行或通用 Agent Skills 生态兼容。
- 本轮完成自动化 API 和前端构建验证，未新增浏览器端导入交互 E2E；后端仍独立执行全部安全校验。
- 本地全量回归不代表真实企业审批、供应链、多人并发发布或生产容量验收。
