# 反思生产闭环增强 TDD 证据

日期：2026-11-05

## 范围

本证据覆盖 `EVAL-REAL`、`ROLLOUT`、`RECOVERY`、`TRACE`、`SEC-REFLECT` 和 `SLO`。用户明确排除真实数据库实例验收；所有数据集均标记为合成数据。

## RED / GREEN

| 能力 | RED 证据 | GREEN 证据 |
|---|---|---|
| 结果级 Gold 评测 | `ModuleNotFoundError: scripts.evaluate_reflection_results` | `tests/test_reflection_result_evaluation.py` 5 passed |
| 租户灰度 | 缺少 `app.domain.reflection_rollout`、`evaluate_health` 和健康 API | 灰度服务/API/审计联合 25 passed |
| 可靠恢复 | 缺少 `app.domain.agent_recovery`、Redis backend 和 runtime writer | 恢复、旧 checkpoint、任务恢复、Agent graph 联合 47 passed |
| Agent trace | 缺少 `app.services.agent_trace_service` | trace 与查询链路联合 55 passed |
| 间接 Prompt 注入 | 缺少 `sanitize_untrusted_context` | 安全评测、Prompt 检测、Critic 联合 17 passed；语料 8/8 |
| SLO 与告警 | 缺少 trace 汇总、反思指标和六类告警 | SLO、审计、告警联合 24 passed；规则结构校验通过 |

## 保证项

| # | 自动化保证 | 测试/命令 |
|---|---|---|
| 1 | 结果评测实际执行候选 SQL 与 Gold SQL，不只比较动作标签 | `tests/test_reflection_result_evaluation.py` |
| 2 | 灰度按租户隔离并稳定分桶，安全绕过强制回滚 | `tests/test_reflection_rollout_service.py` |
| 3 | 同一任务只能有一个有效恢复租约，旧 fencing token 不能提交 | `tests/test_agent_recovery_lease.py` |
| 4 | checkpoint 不保存参数值、结果行，未来 schema version 失败关闭 | `tests/test_agent_recovery_lease.py` |
| 5 | trace 不记录函数参数、结果、Prompt 或异常原文 | `tests/test_agent_trace_service.py` |
| 6 | Unicode、零宽字符和五类间接来源均进入红队回归 | `tests/test_reflection_security_eval.py` |
| 7 | `/metrics` 暴露反思、恢复、预算、Critic 和延迟指标 | `tests/test_agent_slo_metrics.py` |
| 8 | 告警引用的指标存在，阈值明确待企业确认 | `tests/test_alert_rules.py` |

## 最终验证

- 后端：`.venv\Scripts\python.exe -m pytest -q` → `895 passed, 32 skipped, 1 warning`。
- 安全红队：`scripts\evaluate_reflection_security.py` → `8/8`，绕过 0，泄露 0。
- 告警：`scripts\validate_alert_rules.py` → 本地结构通过。
- 覆盖率百分比：当前环境未安装 `pytest-cov`，未声明覆盖率数值。

## 未验收边界

- 120 条结果级与 8 条安全语料是合成回归，不是企业真实准确率或完整攻击覆盖率。
- V023 未在本批真实 MySQL 实例应用。
- Redis 双实例、网络分区和主从切换未真实演练。
- Prometheus `promtool` 和 Alertmanager 通知链路未真实验收。
- Oracle、SQL Server、ClickHouse 真实实例仍未完成。
