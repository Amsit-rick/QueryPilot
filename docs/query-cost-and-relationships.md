# 查询成本与关系元数据

## 查询成本门禁

查询在 Data Gateway 执行前先走数据库方言对应的 `EXPLAIN`：SQLite 使用 `EXPLAIN QUERY PLAN`，MySQL 使用 `EXPLAIN FORMAT=JSON`，PostgreSQL 使用 `EXPLAIN (FORMAT JSON)`。EXPLAIN 只生成计划，不执行用户查询；返回行数不能替代扫描行数。

配置项：

- `QUERY_COST_GUARD_ENABLED`：是否启用成本评估，默认 `true`。
- `QUERY_COST_REQUIRE_EXPLAIN`：适配器不支持 EXPLAIN 时是否阻断，生产建议 `true`。
- `QUERY_COST_MAX_ESTIMATED_ROWS`：估算扫描行数阈值。
- `QUERY_COST_MAX_ESTIMATED_COST`：数据库优化器成本阈值。不同数据库的成本单位不能横向比较，应按目标库基线单独配置。

超过阈值的查询进入现有人工审批，审批执行时会重新 EXPLAIN；成本仍不通过则不会执行 SQL。成本评估失败不会进入模型自动修复循环，返回稳定错误码 `E_QUERY_COST` 或 `E_QUERY_COST_LIMIT`。

查询和审批审计会记录 `cost_backend`、`cost_supported`、`estimated_rows`、`estimated_cost`、`full_scan`、`index_used` 等结构化字段，不记录 EXPLAIN 原始计划、参数值或数据库错误正文。

## 关系元数据

`GET /api/v1/data-gateways/{id}/schema-details` 返回当前身份可见表的主键和外键关系。只读集成工具 `schema.list` 也返回 `relationships`。SQLite 从 `PRAGMA table_info` 和 `PRAGMA foreign_key_list` 读取，MySQL/PostgreSQL 从 information_schema 读取。

关系信息只作为语义上下文，最终 SQL 仍必须通过表/字段白名单、AST 只读校验、行级策略、成本门禁和只读账号。系统不能对没有真实外键、错误业务粒度、软删除规则或快照语义的数据库保证 100% 业务准确；这类关系应通过人工维护的语义条目和评测用例补充，并标记来源与置信度。

没有数据库 FK 的关系可以在 `catalog.json` 的 `relationships` 数组中维护。每条关系至少包含 `from_table`、`from_columns`、`to_table`、`to_columns`，并建议填写 `datasource_id`、`tenant_id`、`version`、`approved`、`confidence`。系统会校验列是否存在、复合关系列数是否匹配，并按数据源和租户过滤；不符合条件的关系不会进入模型或接口响应。

当 `RELATIONSHIP_APPROVAL_REQUIRED=true` 时，涉及未审批关系或低于 `RELATIONSHIP_MIN_CONFIDENCE` 的 JOIN 会进入人工确认；数据库真实 FK 默认视为已由数据库约束确认，人工关系必须显式设置 `approved=true` 才能免于该门禁。

## 可复现实验

在项目根目录运行：

```cmd
.venv\Scripts\python.exe scripts\evaluate_relationship_accuracy.py --output artifacts\relationship-eval.json
```

该评测覆盖一对多聚合、孤儿外键、空维度，以及两个多侧 JOIN 导致重复汇总的四类 SQLite 固定数据。报告分别给出 SQL 语法、关系、聚合粒度、业务结果和是否需要人工确认。报告中的 `external_validation=not_run` 表示它不是企业真实库验收证明。
