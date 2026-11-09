# DUR-05 Conversation Compaction TDD 证据

日期：2026-11-07  
来源计划：`项目改造计划书.md` 的 Durable Agent `DUR-05`

## 用户旅程

1. 用户进行长对话时，模型只接收有界上下文，但任务原始历史仍可完整回放。
2. 用户权限变化后，历史中已撤权的表和字段不再进入模型上下文。
3. 系统能召回与当前问题相关的旧轮次，并保留可追溯的 TaskStep 引用。
4. 历史结果行、参数、原始 SQL、失败 SQL 和模型说明不会被提升为长期事实。

## RED 证据

| 阶段 | 命令 | 实际失败 |
|---|---|---|
| 核心契约 | `.venv\Scripts\python.exe -m pytest tests\test_task_context_service.py -q` | 5 条用例因旧函数不接受 `question` 参数失败 |
| 失败 SQL 排除 | 同上 | `source_turn_count` 实际为 1，预期为 0 |
| API 接入 | `.venv\Scripts\python.exe -m pytest tests\test_api.py -q -k "completed_task_can_continue_with_bounded_follow_up_context or long_follow_up_conversation_records_compaction_stats"` | 2 条用例缺少 `context_version` |
| 全量隔离 | `.venv\Scripts\python.exe -m pytest -q` | 新增长对话用例耗尽进程级测试限流桶，后续 8 条用例收到 429 |
| 极端预算 | `.venv\Scripts\python.exe -m pytest tests\test_task_context_service.py -q` | 单轮宽表上下文为 15505 字符，超过 1000 字符硬预算 |

全量隔离问题通过测试 fixture 在每个用例前后清空本地内存限流桶解决；生产限流默认值和 Redis 限流实现未修改。

## GREEN 证据

| # | 保证 | 测试/命令 | 类型 | 结果 |
|---|---|---|---|---|
| 1 | 结构化 lineage 不携带结果行、参数、原始 SQL 或模型 explanation | `tests/test_task_context_service.py` | 单元/安全 | PASS |
| 2 | 12 轮对话保留 recent turns、压缩旧事实并召回相关库存历史 | `tests/test_task_context_service.py` | 单元 | PASS |
| 3 | 当前 Schema 权限重新过滤 payroll/salary 历史 | `tests/test_task_context_service.py` | 权限 | PASS |
| 4 | 失败查询不进入上下文事实 | `tests/test_task_context_service.py` | 错误路径 | PASS |
| 5 | 4000 字符和极端 1000 字符预算均确定性生效，统计与实际保留项一致 | `tests/test_task_context_service.py` | 边界 | PASS |
| 6 | 10 轮追问记录压缩统计，同时 `/conversation` 仍返回完整 10 轮 | `tests/test_api.py` | API 集成 | PASS |
| 7 | 上下文、会话、能力评测、查询应用和 API 联合回归 | 定向 pytest 命令 | 集成 | `62 passed` |
| 8 | 全部后端行为无回归 | `.venv\Scripts\python.exe -m pytest -q` | 全量 | `1048 passed, 33 skipped, 1 warning` |
| 9 | 工作台多轮对话、刷新恢复和左右消息布局不变 | `npx playwright test e2e/multi-turn-conversation.spec.js` | Playwright E2E | `1 passed` |
| 10 | 前端可生产构建 | `npm run build` | 构建 | PASS |

## 静态与依赖检查

- `.venv\Scripts\python.exe -m compileall -q app scripts`：PASS。
- `.venv\Scripts\python.exe -m pip check`：`No broken requirements found.`
- `git diff --check`：PASS；输出仅为工作区既有 Windows LF/CRLF 转换提示。

## 覆盖与已知缺口

- 当前环境未执行 `pytest-cov`，不声明覆盖率百分比。
- 33 个 skipped 主要是未配置目标数据库真实实例的探针，不计为通过。
- 上下文预算是字符预算，不是供应商 tokenizer 的精确 Token 预算。
- 当前摘要为确定性 SQL lineage facts，当前历史召回为词法/lineage 召回；没有 LLM 摘要或向量历史检索。
- 复杂别名和 CTE 采用安全优先过滤，可能牺牲合法历史召回率。
- 未完成企业超长会话、真实模型、多 Worker 和生产容量验收。
- 本轮没有创建 Git checkpoint commit，因为用户未授权提交；RED/GREEN 证据保存在本文件。
