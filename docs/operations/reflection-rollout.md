# 反思机制灰度与回滚手册

## 1. 适用范围

本文说明 Query Pilot 结果质量反思的本地与部署配置。反思只能在既有权限、租户、SQL AST 安全、Prompt 注入、成本和审批门禁之后工作，不能放宽或覆盖这些硬约束。

当前默认配置为：

```text
AGENT_REFLECTION_MODE=shadow
AGENT_REFLECTION_LLM_ENABLED=false
```

这表示默认仅运行确定性规则并记录建议，不改变线上查询终态，也不调用语义 Critic。

## 2. 三种运行模式

| 模式 | 行为 | 适用阶段 |
|---|---|---|
| `off` | 跳过结果质量反思，不记录结果反思建议；权限和安全硬门禁仍正常执行 | 紧急回滚、基线对照 |
| `shadow` | 执行规则和可选 Critic，保存结构化建议，但不自动修复、澄清或阻断现有结果 | 默认、本地验证、生产观察期 |
| `enforced` | 应用反思动作，可进入重新生成、澄清、审批或失败终态 | 版本化离线门禁通过后的受控灰度 |

`AGENT_REFLECTION_LLM_ENABLED=false` 是 rules-only，不应记为 Critic 故障。设置为 `true` 前必须配置模型凭据，并确认模型请求符合企业数据出境、脱敏和审计要求。

## 3. 离线门禁

在项目根目录使用 Windows CMD 执行：

```cmd
.venv\Scripts\python.exe scripts\evaluate_reflection.py --output artifacts\reflection-eval.json
.venv\Scripts\python.exe scripts\evaluate_reflection_results.py --output artifacts\reflection-result-eval.json
.venv\Scripts\python.exe scripts\evaluate_reflection_security.py --report artifacts\reflection-security-eval.json
.venv\Scripts\python.exe scripts\build_reflection_release_gate.py --action artifacts\reflection-eval.json --result artifacts\reflection-result-eval.json --security artifacts\reflection-security-eval.json --output artifacts\reflection-release-gate.json
```

报告必须同时满足：

- 三份输入报告分别有合法的版本号并通过各自门禁；
- 结果级准确率不低于 `0.95`；
- 安全/权限绕过、敏感值泄漏和危险动作均为 `0`；
- 汇总报告记录三份输入的 SHA-256，运行时只接受 `query-pilot-reflection-release-gate` v1。

当请求 `enforced` 但报告缺失、JSON 无效、门禁失败或存在安全/权限绕过时，运行时会自动降级到 `shadow`。报告必须随候选版本重新生成，不能把旧版本的通过报告长期复用为新版本证据。

仓库内 `data/reflection_eval.json` 是版本化合成用例。它用于验证状态机、分类和灰度门禁可重复执行，不代表真实企业问题、Schema、业务口径或生产数据库上的准确率。

## 4. 灰度流程

1. 在 `off` 和 `shadow` 下分别运行固定评测，核对错误接受率、错误拒绝率、无效重试和安全/权限绕过。
2. 保持 `shadow`，观察 DataTask 的反思 Step/Event、原因码、建议动作和最终状态；抽样人工复核，不只看总通过率。
3. 将确认过的线上 badcase 脱敏后加入版本化评测集，重新运行门禁。
4. 先在测试租户或小流量租户启用 `enforced`，限制自动 SQL 修复、Schema 扩召回和相同 SQL 重试预算。
5. 比较灰度前后的失败率、澄清率、审批率、无效重试、延迟和模型调用成本，再决定是否扩大范围。

当前已经提供租户级版本治理：版本状态为 `draft/canary/active/retired`，灰度按 `tenant_id + subject + version` 计算稳定 SHA-256 百分比分桶。管理 API 支持版本创建、灰度、发布、预览、健康评估和回滚；所有写操作要求 `manage` 权限并写脱敏审计。灰度版本命中前仍会检查离线门禁，不能通过版本配置绕过安全、权限、成本或审批。

健康评估入口为 `POST /api/v1/reflection-rollouts/admin/versions/{version_id}/evaluate-health`。错误接受率、无效重试率、P95 延迟任一超过版本阈值会回滚；安全/权限绕过只要大于 0 就立即回滚。阈值是候选默认值，必须用真实租户基线确认后再用于生产。

## 5. 回滚

最小回滚只修改环境配置，不删除任务、审计或评测证据：

```text
AGENT_REFLECTION_MODE=shadow
AGENT_REFLECTION_LLM_ENABLED=false
```

如需完全关闭结果反思：

```text
AGENT_REFLECTION_MODE=off
AGENT_REFLECTION_LLM_ENABLED=false
```

修改 `.env` 后重启 API。回滚不会关闭权限、SQL 安全、成本和审批门禁。不要删除已有反思 Step/Event；它们是问题回放和审计证据。

## 6. 告警与观察建议

当前项目已经把结构化反思决策写入 DataTask 和审计日志，但以下阈值需要在企业监控平台中按真实基线配置：

- `offline_gate_missing_or_failed` 持续出现或 `enforced` 自动降级到 `shadow`；
- `security`、`permission` 类型出现任何自动接受或自动修复；
- 单任务反思次数接近预算上限；
- `regenerate_sql` 后仍失败的比例明显上升；
- 澄清率、审批率、错误接受率或无效重试率超过基线；
- Critic 超时、非法结构化响应或低置信度比例突增；
- 反思开启后的 P95 延迟和模型成本超过已批准预算。

告警中只记录租户/任务标识、原因码、动作、次数、SQL 哈希和稳定错误码，不记录完整 SQL、参数、结果行、数据库错误原文或模型响应原文。

## 7. Badcase 纳入流程

1. 任务属主提交反馈后只生成 `draft` 离线案例，不直接进入回归集。
2. 管理员通过 `/api/v1/quality/admin/tasks/{task_id}/feedback/{feedback_id}/review` 批准或拒绝；审核记录不可变并写审计。
3. 只有 `approved` 案例会物化为 `approved_evaluation_case`，并绑定模型、Prompt、Skill、反思策略和评测集版本。
4. 使用 `/api/v1/quality/admin/evaluation-datasets/{dataset_version}` 导出版本化数据集，仍不得包含结果行、参数值或凭据。
5. 先确认旧版本能够复现 badcase，再实现修复并重跑动作、结果、安全三套门禁。
6. 门禁通过后进入 `shadow` 观察；系统不会根据反馈自动修改 Prompt、Skill 或业务口径。

## 8. 已知边界

- 合成评测没有证明真实业务准确率、生产容量或多租户灰度效果。
- Critic 只接收授权 Schema、关系摘要、脱敏 SQL AST/EXPLAIN 和结果统计；不接收原始结果值。
- 当前评测脚本中的 Token、成本和延迟是确定性夹具估算，不是在线模型账单或生产 P95。
- `reflection-result-eval-v1` 和 `reflection-security-eval-v1` 均为合成回归，不是企业真实准确率或完整攻击覆盖率证明。
- V023 MySQL 灰度版本迁移尚未在本批真实 MySQL 实例应用验收。
- Oracle、SQL Server、ClickHouse 仍缺少真实实例完整验收；反思专项完成不改变该状态。

