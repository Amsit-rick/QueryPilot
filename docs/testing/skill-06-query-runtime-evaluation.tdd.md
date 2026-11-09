# SKILL-06 查询运行时与版本评测 TDD 证据

日期：2026-11-04

## 用户旅程

- 管理员为现有 Skill 编辑触发词、意图和步骤，先评测候选声明，再创建草稿并执行灰度/发布。
- 用户提交自然语言查询时，当前租户生效的 Skill 声明进入 SQL 生成上下文；无命中则保留原查询上下文。
- 查询任务可以追溯实际使用的 Skill ID、版本、状态和触发证据。

## RED

- `pytest tests/test_skill_service.py -q`：新增的路由版本证据测试失败，原因是路由结果缺少 `selected_skill.deployment`；其余 10 条通过。
- `pytest tests/test_skill_api.py::test_admin_can_compare_candidate_skill_against_fixed_routing_cases tests/test_skill_api.py::test_non_manager_cannot_evaluate_skill_candidate -q`：两个测试均因候选评测 API 不存在返回 404。
- 前端初次用默认 Playwright 浏览器启动失败，因本机未安装 Playwright 管理的 Chromium；这不是应用行为失败，随后使用本机 Chrome 完成浏览器回归。

## GREEN

实现了 Skill 路由版本/部署证据、结构化生成上下文、查询任务与响应追踪、声明编辑器、长度校验、管理员固定集评测 API/页面及离线 CLI。内部查询路由不额外写独立路由审计，避免同一查询请求被计数两次；独立路由入口仍保留审计。

实际验证：

```text
.venv\Scripts\python.exe -m pytest tests/test_skill_api.py tests/test_skill_evaluation.py tests/test_skill_service.py tests/test_api.py -q
63 passed

.venv\Scripts\python.exe -m pytest -q
777 passed, 11 skipped, 1 warning

frontend: npm run test:e2e -- --grep "管理员创建 Skill 版本"
1 passed (本机 Chrome；测试包含 1440/768/390px 横向溢出断言)

frontend: npm run build
passed

.venv\Scripts\python.exe scripts\evaluate_skill_routing.py --candidate data\skill-candidate.example.json
10 cases; baseline matches 9; candidate matches 10; route changes 1; version changes 3
```

## 保证与边界

| 保证 | 测试/证据 | 结果 |
|---|---|---|
| Skill 命中时返回已解析版本和部署状态 | `tests/test_skill_service.py` | PASS |
| SQL 生成器收到选中 Skill 的步骤声明 | `tests/test_api.py::test_selected_skill_declaration_reaches_sql_generator` | PASS |
| 查询响应和任务详情保存 Skill 版本 | `tests/test_api.py::test_query_records_skill_release_evidence_in_task_and_response` | PASS |
| 普通用户不能调用候选评测 API | `tests/test_skill_api.py::test_non_manager_cannot_evaluate_skill_candidate` | PASS |
| 管理员能比较当前目录和候选定义 | `tests/test_skill_api.py::test_admin_can_compare_candidate_skill_against_fixed_routing_cases` | PASS |
| 编辑声明后可查看评测并走完发布治理页面 | `frontend/e2e/skill-release.spec.js` | PASS |

评测仅验证确定性路由命中，不测 SQL 语法、执行、业务口径或模型对步骤的遵循率。固定集目前只有 10 条本地问句，生产效果仍需企业问句和 gold SQL/结果集。Skill 步骤是送入生成上下文的声明，不是图节点级确定性工作流。

当前工作区在任务开始前已有大量未提交、跨多个功能文件的修改。由于本次关键文件与这些修改重叠，未创建 TDD checkpoint commit，以免误提交用户现存改动；本证据记录保留 RED/GREEN 结果供后续合并审阅。
