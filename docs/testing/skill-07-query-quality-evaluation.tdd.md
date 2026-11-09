# SKILL-07 SQL 与查询结果质量评测 TDD 证据

日期：2026-11-04

## 验收目标

管理员编辑 Skill 草稿后，可在发布前比较基线与候选的 SQL 生成、既有 SQL 护栏、隔离合成库执行和精确结果匹配。普通用户不能访问评测 API；没有模型 Key 时明确失败，不能把凭据带入响应。

## SKILL-07 初始实现与隔离

- 初始固定数据集：`data/skill_query_eval.json`，3 条 gold SQL/预期列/预期行（已由 SKILL-08 扩充到 10 条）。
- 核心实现：`app/skill_query_evaluation.py`；gold 结果先自校验，评测数据库使用临时目录和 SQLite `mode=ro`，开启 `query_only`，5 秒 progress deadline，最多读取 101 行以检测 100 行截断。
- API：`POST /api/v1/skills/admin/evaluate-query-quality`，复用管理员 `manage` 依赖及当前租户模型运行时配置。
- 前端：`/admin/skills` 的“评测 SQL 效果”动作。草稿变更后旧报告清空；只显示指标、用例状态与版本元数据，不显示候选 SQL 或完整结果行。

## 自动化验证

| 场景 | 验证 | 结果 |
|---|---|---|
| 候选结果正确性高于基线 | `tests/test_skill_query_evaluation.py::test_query_quality_eval_compares_validated_rows_for_baseline_and_candidate` | PASS |
| 写 SQL 在执行前被既有护栏拒绝 | `tests/test_skill_query_evaluation.py::test_query_quality_eval_rejects_unsafe_model_sql_before_fixture_execution` | PASS |
| 管理员 API 比较基线/候选且不调用真实外部模型 | `tests/test_skill_api.py::test_admin_can_compare_sql_results_for_skill_versions_without_external_model` | PASS |
| 未授权用户不能调用结果质量评测 | `tests/test_skill_api.py::test_non_manager_cannot_run_skill_sql_quality_evaluation` | PASS |
| 缺模型 Key 返回 409 且响应不泄漏模型标识/凭据 | `tests/test_skill_api.py::test_query_quality_evaluation_requires_configured_model_key_without_leaking_secrets` | PASS |
| 草稿路由评测、SQL 结果对比、发布治理和窄屏无溢出 | `frontend/e2e/skill-release.spec.js` | PASS |

## 执行结果

- `.venv\Scripts\python.exe -m pytest tests\test_skill_query_evaluation.py tests\test_skill_api.py -q`：`12 passed`。
- `.venv\Scripts\python.exe -m pytest -q`：`782 passed, 11 skipped, 1 warning`。警告为既有 Starlette TestClient cookie API 弃用提示。
- `frontend` 目录 `npm run build`：成功。
- `frontend` 目录 `PLAYWRIGHT_EXECUTABLE_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe npm run test:e2e -- --grep "管理员创建 Skill 版本"`（Windows PowerShell 通过环境变量运行）：`1 passed`。

## 限制与下一步

评测器只对合成 SQLite 数据执行问题。`generation_attempts` 表示生成器调用尝试数，不代表真实 provider HTTP 请求数；模型缓存可能减少外部请求。精确结果匹配不是业务指标口径认证，也不是企业库准确率。SKILL-08 的 10 条案例为人工构造模拟数据，不能替代脱敏真实 badcase 和业务确认；独立多事实表预聚合与真实数据源评测仍未完成。

## SKILL-08 合成评测扩展

- 数据集升级为 `2026-11-04-v2`，包含 10 条问句，新增订单、订单明细、商品、支付记录和无订单客户数据；生成器上下文和 SQL 白名单 Schema 统一扩展为 6 张表。
- 新增测试先 RED：分类/用例数测试失败（缺 category，只有 3 条）；gold 执行测试失败揭示 SQL 护栏误拒绝 `AND` 逻辑运算符。修复护栏节点分类后定向测试 `45 passed`。
- `.venv\Scripts\python.exe -m pytest -q`：`785 passed, 11 skipped, 1 warning`。
- `frontend` 目录 `npm run build` 成功；本机 Chrome Playwright Skill 页面 `1 passed`，覆盖 10 案例显示、各项指标、桌面/平板/390px 窄屏和评测中草稿变更时丢弃过期结果。
- 所有 10 条 gold SQL 在临时只读 SQLite 上得到精确预期列与结果；该结果只验证本地模拟 fixture 自洽，不证明模型质量或企业业务口径。未调用真实模型。
- 覆盖率工具未安装：`pytest --cov=...` 提示 `--cov` 参数不可用，`python -m coverage --version` 提示模块不存在；因此本批没有覆盖率百分比证据。
- 未创建 TDD checkpoint commit：当前共享工作区已含大量既有变更，`app/guardrails.py` 和测试文件亦非本任务独占，避免把无关用户变更一并提交。

## 多条件护栏运行态复核

- 回归覆盖扩展到 SQLite、MySQL、PostgreSQL 三方言：普通 `AND`/`OR` 条件应通过，逻辑条件中包含未知函数仍必须拒绝。
- `.venv\Scripts\python.exe -m pytest tests\test_guardrails.py tests\test_skill_query_evaluation.py tests\test_sql_security_eval.py -q`：`41 passed`。
- `.venv\Scripts\python.exe scripts\evaluate_sql_security.py`：安全语料 `25/25`，包含未知函数、文件函数、延时函数、堆叠语句和危险 SQL 拒绝用例。
- 运行态检查确认 Query Pilot API 在 `127.0.0.1:8001` 的 Uvicorn 进程于代码修改前启动且未启用 reload；健康端点为 200，但这不能证明其已载入新代码。尝试由当前工具重启既有进程被执行策略拒绝，因此用户需在原 API 终端结束旧进程并按文末命令重新启动后，再验证浏览器当前进程。

## SKILL-09 非递归 CTE 与双事实表预聚合

- 用户旅程：管理员希望在发布 Skill 前验证“订单明细金额”和“分笔收款金额”两个一对多事实侧不会在直接 JOIN 时相互放大。
- RED：数据集仍为 v2/10 条，缺少多事实用例；共享 SQL 护栏将 CTE 名称当作未授权物理表，并且递归 CTE 的错误码不准确。
- GREEN：护栏允许最多 8 个非递归、名称不覆盖业务表、输出列唯一明确的只读 CTE；物理表、字段、函数、JOIN 和参数仍按原安全边界校验，递归或自引用 CTE 拒绝。
- 数据集升级为 `2026-11-04-v3`/11 条。新增用例先分别按订单聚合 `order_items` 与 `payments`，再连接已支付订单；固定数据中订单 101 正确结果为商品金额 250、收款金额 250，错误直连会同时放大到 500。
- 单次质量评测上限由 10 条扩到 20 条，当前 11 条会产生 22 次基线/候选生成尝试；页面说明不再写死用例数量。
- 该用例是合成关系与固定业务口径，不证明真实企业多事实模型全部正确。真实库仍需业务方确认粒度、退款、税费、软删除、快照和状态口径。
- GREEN 验证：核心护栏/质量评测/安全语料 `44 passed`，API/Agent 集成 `93 passed`，后端全量 `793 passed, 11 skipped, 1 warning`，安全语料 `25/25`，前端生产构建通过。
- 本轮未启动浏览器 E2E：用户要求 Query Pilot 保持关闭且另一项目正在运行，避免测试 Web Server 抢占端口；前端改动仅为文案和 E2E mock 数量同步。
