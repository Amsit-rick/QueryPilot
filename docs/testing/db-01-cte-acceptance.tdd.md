# DB-01+ 非递归 CTE 真实验收增强 TDD 证据

日期：2026-11-04

## 用户旅程

数据库验收人员需要确认 Oracle、SQL Server、ClickHouse 不仅能执行简单 SELECT，还能在真实实例上执行 Text-to-SQL 常用的非递归 CTE 预聚合，同时保持参数绑定、只读权限和错误脱敏边界。

## RED

- 三种连接器的本地 CTE 契约已经能执行，但 `connector_acceptance.py` v4 没有 `cte_preaggregation` evidence。
- 新测试读取该证据时得到 `KeyError`；定向结果为 `1 failed, 87 passed`。
- 这说明未来即使 live case 通过，原报告也无法证明 CTE 曾在目标实例上运行。

## GREEN

- live case 新增按区域 `SUM(amount)` 的非递归 CTE，并使用 `:region` 参数；目标实例必须返回结果。
- 报告升级为 v5，implementation 和 live evidence 分离；本地实现存在不等于真实 evidence 通过。
- Oracle、SQL Server、ClickHouse mock 契约验证目标方言编译与参数绑定。
- 递归 CTE继续由共享护栏拒绝；远程 SQL 对 `AND`/`OR` 使用 AST 连接节点判断，不将其视为函数。
- 定向命令：`.venv\Scripts\python.exe -m pytest tests\test_connector_acceptance.py tests\test_remote_gateways.py tests\test_guardrails.py -q`
- 结果：`126 passed`。
- 后端全量：`799 passed, 11 skipped, 1 warning`。
- 真实 live 文件：`3 skipped`，三种目标实例均未配置；未计入通过。

## 限制

本机没有三种目标数据库实例，因此本批没有产生 `external_validation=passed`。真实验收仍必须使用独立只读账号、合成 `sales` 表和零行写入拒绝探针执行，不能以 mock 或 SQLite 结果替代。

## v6 逐证据验收

- RED：原执行器只调用一个单体测试，成功摘要为 `1_passed`；模拟 CTE 探针失败时没有命中独立测试名，报告仍错误地保持整体通过。
- GREEN：八项 evidence 分别调用独立 pytest node；部分失败报告保留 7 项 true、仅 `cte_preaggregation=false`，汇总为 `7_passed,1_failed`。
- 本地契约与采集测试：`89 passed, 24 skipped`。24 个 skipped 是未配置的 3 个目标实例 × 8 个独立探针，不计为外部通过。
- 后端全量：`800 passed, 32 skipped, 1 warning`；新增 skipped 均为显式外部环境探针。
- 报告格式升级为 v6；整体通过仍要求八项全部通过。
