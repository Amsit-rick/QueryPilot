---
schema_version: query-pilot-skill-import/v1
id: sales-analysis
version: 1.1.0
name: 销售分析
description: 增加毛利口径和销售趋势触发词的受控声明版本。
intents:
  - sales_query
  - gross_margin_query
trigger_terms:
  - 销售
  - 营收
  - 毛利
allowed_tools:
  - schema.list
  - query.plan
  - result.get
workflow_steps:
  - 确认销售指标与时间口径
  - 生成受控只读查询计划
allowed_roles:
  - viewer
  - analyst
  - admin
read_only: true
priority: 70
---

# 人工说明

此正文不会进入 Skill 定义，也不会作为脚本或系统提示词执行。导入前需要在管理页面填写来源、修订版本和发布者，
系统会在浏览器端计算 SHA-256，并要求管理员先预览差异，再显式确认创建草稿。
