# 受控评测集

## 运行

在项目根目录执行：

```cmd
.venv\Scripts\python.exe -m app.eval
```

同一条命令会同时运行 SQL fixture 和跨能力契约评测。若只需要后者，可执行：

```cmd
.venv\Scripts\python.exe -m app.capability_eval
```

评测默认读取 `data/eval.json`，并以只读方式执行 SQL 到 `data/demo.db`。数据库中的
数据可以是演示数据或企业合成数据，因此业务结果断言应优先写列名和稳定的空结果；
依赖固定样本行的断言必须随数据版本一起更新。

## 三类证据

- `guardrail`：SQL 是否按用例预期被接受或拒绝。攻击用例的正确拒绝会计入该指标，
  但不代表 SQL 本身有效。
- `execution`：通过校验后的 SQL 是否能在只读 demo 数据库执行，不能替代 MySQL、
  PostgreSQL 或 Oracle 真实连接验收。
- `model_generation`：只有用例提供 `generated_sql` 时才统计；当前仓库中的候选是
  冻结 fixture，不代表实时模型调用质量。未提供候选时 `evaluated` 为 0。
- `business_correctness`：只有用例提供 `expected_columns` 或 `expected_rows` 时才
  统计。列名相同不等于业务口径正确，正式验收还需要业务负责人确认指标定义。

报告还会按 `golden`、`boundary`、`adversarial` 分组，并输出 `failure_attribution`，
用于区分执行失败、模型候选不一致、业务期望不符和安全拦截未生效。

## 数据集约定

`data/eval.json` 当前版本为 `2026-09-15-v2`，包含 50 条 golden、15 条 boundary 和
10 条 adversarial 用例，覆盖聚合、筛选、排序、分页、日期、CASE、JOIN、空结果、参数
绑定、未知表/字段、笛卡尔积和写操作诱导。数据全部为合成数据，不包含凭据或生产记录。

每次模型、Prompt、语义目录或护栏变更都应更新版本号并保存完整 JSON 报告；不要只报告
一个没有分母的“准确率 100%”。

## 跨能力契约集

`data/capability_eval.json` 当前版本为 `2026-11-01-v4`，包含 24 条本地确定性用例，覆盖
语义别名检索、角色字段过滤、追问上下文不携带结果行、图表配置白名单、审批状态机、只读
集成工具清单和未知数据库拒绝，以及 Oracle/SQL Server/ClickHouse 方言转换与参数绑定、
跨 schema 拒绝、SELECT INTO 拒绝、文件表函数拒绝和角色字段隔离。
新增 2 条字段值 Grounding 用例，分别验证授权字段可以把“华东”召回为标准值 `EAST`，以及目标字段
未授权时标准值和其他词典内容均不进入召回结果。
另有 3 条智能分析用例覆盖趋势、上期/同比比较，以及重复时间点和截断结果的保守限制说明；
3 条质量闭环用例覆盖低置信度门禁、候选异常审批和离线反馈脱敏。
连接器评测只生成并校验 SQL，不打开数据库连接；各用例明确 `execution=not_run`。
报告的 `external_validation` 固定标识本地评测没有执行实时
模型、远程 MCP、企业数据库或生产审批环境；这些环境仍需单独验收。
# Business Gold Question / SQL / Result

`data/skill_query_eval.json` 同时作为版本化合成 Business Gold 数据集，包含 11 条问题、Gold SQL、
期望列和精确结果，覆盖聚合、排序、JOIN、LEFT JOIN、日期/状态过滤、一对多扇出和双事实预聚合。

```cmd
.venv\Scripts\python.exe scripts\evaluate_business_gold.py --dataset data\skill_query_eval.json --output artifacts\business-gold-eval.json
```

脚本先在隔离只读 SQLite fixture 中验证 Gold 自身完整性，再可选调用当前模型生成 SQL，经过同一 AST
护栏后执行并精确比较结果。无模型密钥时 `external_validation=not_run`；正式发布增加
`--require-external`。数据集明确标记 `synthetic=true`，不能作为企业真实准确率。

## 真实 MySQL 业务评测（40 条）

`data/business_gold_mysql.json` 是本机 50 万级企业演示库上的 40 条中文业务问题。它与上面的 fixture 评测
分开统计：Gold SQL 和模型 SQL 都跑在同一个真实只读 MySQL 上，模型调用沿用生产链路，
端到端一致率把生成失败、护栏拒绝、执行失败和结果不一致都计为未命中。

```cmd
.venv\Scripts\python.exe scripts\evaluate_business_gold_mysql.py --check-gold
.venv\Scripts\python.exe scripts\evaluate_business_gold_mysql.py --output artifacts\business-gold-mysql.json
```

2026-11-08 基线：40/40 Gold 完整，4 次重复运行端到端一致率 72.5%～77.5%（中位数 73.8%）。
方法论、失败分布和边界见 [`docs/testing/business-gold-mysql-eval.md`](testing/business-gold-mysql-eval.md)，
单次证据见 [`docs/evidence/business-gold-mysql.json`](evidence/business-gold-mysql.json)。

