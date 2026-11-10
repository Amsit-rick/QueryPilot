# 多月演示数据 TDD 证据

## 范围

本轮针对 PostgreSQL 和本机 Oracle 的单月演示数据不足问题，补充可重复的多月分析数据，并保证 PostgreSQL 数据卷重新创建后可由标准多数据源启动流程自动恢复。

## 用户旅程

- 作为演示用户，我希望 PostgreSQL 和 Oracle 至少包含连续 12 个月数据，以便趋势、环比和异常波动分析有足够时间点。
- 作为本地开发者，我希望重复执行种数或重复启动项目时不会无限追加数据。
- 作为运维人员，我希望通用启动脚本不自动对可能是远程实例的 Oracle 执行 SYSDBA 写入。

## RED / GREEN

| 阶段 | 命令 | 结果 | 证明内容 |
| --- | --- | --- | --- |
| RED 1 | `.venv\Scripts\python.exe -m pytest tests\test_seed_multimonth_demo_data.py -q` | 测试收集失败，种数模块尚不存在 | 多月数据生成与两种数据库 Upsert/Merge 尚未实现 |
| GREEN 1 | 同上 | `4 passed` | 12 个月/48 条、确定性波动、PostgreSQL Upsert、Oracle Merge 与标识符校验通过 |
| RED 2 | `.venv\Scripts\python.exe -m pytest tests\test_local_start_scripts.py -q` | `1 failed, 6 passed` | PostgreSQL 启动流程未等待 ready，也未自动补齐多月数据 |
| RED 3 | 同上 | `1 failed, 6 passed` | 初版括号块等待方式缺少明确、可验证的等待契约 |
| 运行态 RED | `cmd.exe /d /c scripts\start_all_datasources.cmd` | `The system cannot find the batch label specified - wait_postgres` | 静态字符串测试无法证明批处理标签在真实 CMD 中可调用 |
| RED 4 | 联合 pytest | 测试收集失败，缺少 `_wait_for_postgres` | 等待职责迁移到 Python 前，新的可测试等待契约尚未实现 |
| GREEN 2 | `.venv\Scripts\python.exe -m pytest tests\test_local_start_scripts.py tests\test_seed_multimonth_demo_data.py -q` | `12 passed` | Python `pg_isready` 有界重试、启动入口和两库幂等种数逻辑全部通过 |
| 运行态 GREEN | `cmd.exe /d /c scripts\start_all_datasources.cmd` | 六数据源全部 `READY` | PostgreSQL 自动种数成功，随后 SQLite/MySQL/PostgreSQL/Oracle/SQL Server/ClickHouse 真实只读验收全部通过 |
| 发布前 RED/GREEN | `.venv\Scripts\python.exe -m pytest tests\test_seed_multimonth_demo_data.py -q` | 缺少 `_resolve_oracle_home` 后修复为 `6 passed` | 公共脚本不再包含本机 Oracle 绝对路径，必须使用 `--oracle-home` 或 `ORACLE_HOME` |

## 真实数据库证据

2026-11-10 对同一命令重复执行后，保留 ID 范围的聚合结果仍为：

| 数据库 | 记录数 | 月份数 | 起始月份 | 结束月份 |
| --- | ---: | ---: | --- | --- |
| PostgreSQL 16 | 48 | 12 | 2025-12 | 2026-11 |
| Oracle 19c | 48 | 12 | 2025-12 | 2026-11 |

实际应用链路提交“分析每月销售趋势并找出波动最大的月份”时，两库均返回 12 行月度聚合并生成趋势结果。当前意图路由把“波动”归为 `TREND`，因此 API 返回趋势但不会同时填充异常点列表；这属于后续分析意图增强项，不影响本轮数据完整性。

## 安全与边界

- PostgreSQL 使用固定容器内专用演示账号执行 `ON CONFLICT DO UPDATE`。
- Oracle 使用 `MERGE`，仅允许经过校验的 PDB 和 Schema Owner 标识符。
- 通用启动脚本不自动调用 Oracle SYSDBA；本机种数必须由管理员显式执行。
- 本轮数据是确定性模拟数据，不代表真实企业业务分布或模型生产准确率。
- 未创建 TDD checkpoint commit：当前工作区包含大量既有未提交修改，且用户未授权提交或推送。
