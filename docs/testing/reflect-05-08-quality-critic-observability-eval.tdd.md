# REFLECT-05～08 结果质量、Critic、可观测性与灰度 TDD 证据

## 范围

来源计划：`docs/reflection-mechanism-roadmap.md`。

本批完成确定性结果质量验证、受控语义 Critic、DataTask/审计/前端反思轨迹，以及版本化离线评测与 `off/shadow/enforced` 灰度门禁。`REFLECT-00～04` 的核心运行时证据见 `reflect-00-04-core-runtime.tdd.md`。

## 用户旅程

1. 查询执行后，系统先用确定性规则检查维度、指标、Top N、时间范围、聚合扇出、空结果、截断和结果形状。
2. 只有规则无法确定的业务语义问题才允许调用结构化 Critic，且 Critic 不能修改权限、安全、成本或审批策略。
3. 每次反思以结构化 Step/Event 和审计记录保存；viewer 只能看到用户可读摘要和非敏感字段。
4. `shadow` 只记录建议，`enforced` 只有在版本化离线报告通过时才生效，否则自动降级。
5. 多轮对话的每次查询创建新的 Agent State，反思预算不跨轮继承。

## RED

后端首次 RED 包括：

```text
ModuleNotFoundError: No module named 'app.services.result_quality_service'
ModuleNotFoundError: No module named 'app.services.semantic_critic_service'
ModuleNotFoundError: No module named 'app.services.reflection_trace_service'
ModuleNotFoundError: No module named 'app.services.reflection_mode_service'
```

后续安全和边界测试还暴露：

- Critic 上下文包含当前 SQL 未引用的其他授权表；
- 调用 Critic 前没有复用 Prompt 注入检测；
- rules-only 模式会把“Critic 未启用”误记为模型故障；
- 低置信度或冲突动作缺少统一保守回退。

这些失败推动实现范围收紧到“当前 SQL 引用且已授权的 Schema、脱敏统计和结构化动作”。

前端最初两次失败分别来自本机浏览器路径和移动导航测试设置，不属于功能 RED。为了验证用例确实能够发现 UI 回归，另外执行了可恢复 mutation check：临时移除“结果质量反思”标签后，Playwright 在 `getByText('结果质量反思')` 正确失败；恢复源码后用例通过。该检查只证明测试敏感性，不冒充开发前 RED。

## GREEN

核心实现：

- `ResultQualityService` 完成问题覆盖度、聚合粒度、JOIN 扇出、空结果、截断、重复维度和业务口径不确定性规则。
- `SemanticCriticService` 使用独立结构化 Prompt；HTTP 客户端禁用环境代理、禁止重定向、不自动重试，也不记录 Prompt、Key 或响应原文。
- `ResultReflectionCoordinator` 统一 rules-only、rules+critic、shadow 和 enforced 行为；用户显式 SQL 不允许被结果反思静默替换。
- bounded 与 LangGraph 都支持异步结果验证，并继续受原有分类型预算、安全、审批和成本门禁约束。
- `ReflectionTraceService` 将失败类型、原因码、动作、次数、证据摘要、SQL SHA-256、模型和 Prompt 版本写入 DataTask；viewer 投影删除内部证据、SQL 哈希和模型信息。
- 任务详情页增加“结果质量反思”时间线，窄屏保持无横向溢出。
- `scripts/evaluate_reflection.py` 比较 `off`、`rules_only`、`rules_plus_critic`；`ReflectionModeService` 在 enforced 门禁缺失或失败时降级到 shadow。

本批开发阶段的定向回归：

```text
.venv\Scripts\python.exe -m pytest tests\test_result_quality_reflection.py tests\test_semantic_critic.py tests\test_result_reflection_coordinator.py tests\test_reflection_trace.py tests\test_reflection_evaluation.py tests\test_query_application_service.py -q
26 passed
```

开发阶段全量基线：

```text
.venv\Scripts\python.exe -m pytest -q
861 passed, 32 skipped, 1 warning
```

32 个 skipped 是 Oracle、SQL Server、ClickHouse 未配置真实实例的探针，不计为通过。warning 是既有 Starlette Cookie 测试 API 弃用提示。

前端与浏览器：

```text
npm run build
3 passed
```

三个关键 Playwright 流程为反思时间线、任务分析和多轮追问；单独反思用例也通过，390px 视口无横向溢出。

## 固定评测结果

执行：

```cmd
.venv\Scripts\python.exe scripts\evaluate_reflection.py --output artifacts\reflection-eval.json
```

当前 `reflection-eval-v1` 共 11 条合成用例：

| 模式 | 分类准确率 | 错误接受率 | 错误拒绝率 | 无效重试率 | 安全/权限绕过 |
|---|---:|---:|---:|---:|---:|
| off | 45.45% | 33.33% | 0 | 0 | 0 |
| rules-only | 90.91% | 0 | 50% | 0 | 0 |
| rules+critic fixture | 100% | 0 | 0 | 0 | 0 |

候选 `rules+critic` 门禁通过。关闭反思会错误接受部分 badcase；rules-only 对一条需要业务口径确认的正确查询采取了保守澄清，因此存在错误拒绝。`rules+critic` 使用评测文件中记录的结构化 Critic 夹具，不是在线调用真实模型；Token、成本和延迟字段也是评测估算。因此这些数字只能证明版本化合成集上的可重复行为，不能作为企业真实业务准确率。

## 安全与隐私保证

| 保证 | 主要测试/证据 | 结果 |
|---|---|---|
| Critic 只接收当前 SQL 引用的授权 Schema | `test_semantic_critic_prompt_contains_no_rows_literals_or_secrets` | PASS |
| Prompt 注入在 Critic 前被拒绝 | `test_semantic_critic_prompt_guard_blocks_injection_before_model_call` | PASS |
| 无 Key、超时和非法 JSON 保守处理 | `tests/test_semantic_critic.py` | PASS |
| 用户显式 SQL 不被静默重写 | `tests/test_result_reflection_coordinator.py` | PASS |
| viewer 隐藏证据、SQL 哈希和模型字段 | `test_viewer_task_detail_redacts_internal_reflection_evidence` | PASS |
| 审计不保存完整 SQL、参数、结果或错误原文 | `test_reflection_audit_records_safe_decision_metadata_only` | PASS |
| enforced 缺少通过报告时降级 shadow | `tests/test_reflection_evaluation.py` | PASS |
| SQL 安全语料保持通过 | `scripts/evaluate_sql_security.py` | 25/25 |

## 覆盖率与限制

当前环境未安装 `pytest-cov`，本批没有覆盖率百分比证据，不能用通过用例数代替覆盖率。

本批完成的是本地代码、自动化测试、前端展示和合成评测门禁。真实企业口径、真实线上 badcase、在线 Critic 稳定性、生产延迟/成本、真实多数据库实例和企业监控阈值仍需外部环境验收。

## Git 证据

共享工作区已有大量跨模块未提交修改，本批没有自动提交或清理无关文件。实现、测试和文档通过独立文件与命令记录，避免把用户现存改动混入 checkpoint commit。

