# 质量反馈闭环

## 自动人工确认

查询继续复用现有 `query_approvals` 和 `approval_required` 状态机。以下条件会自动创建审批单：

- 模型计划返回 `needs_approval=true`。
- 模型提供了有效置信度，且低于 `AGENT_LOW_CONFIDENCE_THRESHOLD`。
- SQL 使用通配符投影，或请求行数超过 `AGENT_HIGH_RISK_ROW_THRESHOLD`。
- 确定性分析实际识别到候选异常。

低置信度和高风险门禁发生在 Data Gateway 执行前。候选异常只能在受权且脱敏的结果上识别；
识别后不发布表格、图表或异常结论，任务进入审批。管理员批准时系统按申请人的最新权限重新校验、
重新执行并生成结果资产。审批拒绝、过期和权限变化继续沿用原有失败与恢复规则。

置信度为 `0` 表示提供商没有返回置信度，不会被误判为低置信度。direct 适配器优先请求结构化
`SqlPlan`，并兼容只返回裸 SQL 的旧模型。

## 用户反馈

任务属主可提交四种反馈：`accepted`、`corrected`、`rejected`、`failed`。前三种只适用于已完成任务，
`failed` 只适用于失败任务。修正反馈必须提供只读 SQL，并再次经过当前数据源 Schema、角色字段权限
和只读校验。

```http
POST /api/v1/tasks/{task_id}/feedback
Content-Type: application/json

{
  "outcome": "corrected",
  "note": "应按区域聚合",
  "corrected_sql": "SELECT region, SUM(amount) FROM sales GROUP BY region"
}
```

反馈作为不可变任务事件保存；每条反馈同时生成一个 `evaluation_case` 任务资产。该资产只保存脱敏问题、
去除字面量的 SQL 模板、SQL 哈希、结果状态和错误码，不接收或保存结果行、数据库凭据、参数值。
读取仍通过任务的租户和属主检查：

```http
GET /api/v1/tasks/{task_id}/feedback
```

反馈内容出现密码、密钥、令牌或带认证信息的连接 URI 时请求会被拒绝。邮件、手机号和 SQL 字面量会
在离线案例中替换为脱敏占位符。离线案例是本地确定性评测素材，不等同于业务负责人确认的黄金答案。

### 管理员审核与版本化回归集

用户提交的 `evaluation_case` 默认状态为 `draft`，不会自动进入回归集。具备 `manage` 权限的管理员审核：

```http
POST /api/v1/quality/admin/tasks/{task_id}/feedback/{feedback_id}/review
Content-Type: application/json

{
  "decision": "approved",
  "dataset_version": "feedback-regression-v1",
  "note": "业务口径已确认"
}
```

批准后生成不可变 `approved_evaluation_case`，记录审核人、审核时间以及原查询使用的模型、Prompt、Skill、反思策略版本。拒绝案例只保留审核事件，不进入数据集。管理员可读取指定版本：

```http
GET /api/v1/quality/admin/evaluation-datasets/feedback-regression-v1
```

该闭环只生成可回归素材，不会自动修改 Prompt、Skill、模型配置或业务口径。

管理员可触发当前租户正在使用的模型回放指定数据集：

```http
POST /api/v1/quality/admin/evaluation-datasets/feedback-regression-v1/regressions
```

回放先解析结构化计划，再经过当前 SQL AST 只读、表字段和函数白名单校验；随后比较表、字段、聚合、
分组、排序、过滤和 JOIN 结构。只有原查询结果预览完整、问题未因隐私脱敏而改变且存在不可逆结果摘要时，
才执行结果摘要比对。报告不保存生成 SQL、参数值、结果行、供应商原始响应或异常原文。

缺少当前模型密钥时返回 `external_validation=not_run`，不会使用历史 SQL 冒充当前模型通过。离线导出集可运行：

```cmd
.venv\Scripts\python.exe scripts\evaluate_feedback_regression.py --dataset data\feedback_regression_eval.json --output artifacts\feedback-regression.json
```

正式发布要求外部模型实际执行时增加 `--require-external`。仓库自带的 CI seed 标记为 `synthetic=true`，
只验证报告和门禁链路，不等同管理员审核的企业 badcase。

## MySQL 迁移

`V016__add_quality_approval_context.sql` 为审批单增加触发类型、原因列表和分析模式。部署时继续使用：

```cmd
.venv\Scripts\python.exe scripts\mysql_migrate.py
```

迁移只扩展审批上下文，不创建第二套审批表。
