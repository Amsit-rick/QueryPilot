# Oracle owner 元数据与真实关系验收 TDD 证据

## 目标

管理员为 Oracle 数据源配置业务 Schema owner 后，系统应只召回该 owner 下白名单范围内的表、字段和主外键关系；
执行查询时继续使用只读账号，但将会话当前 Schema 切换到业务 owner。owner 名称必须先经过严格标识符校验。

## RED

- 命令：`.venv\Scripts\python.exe -m pytest tests\test_remote_gateways.py -q --disable-warnings -k "schema_owner"`
- 结果：`6 failed, 82 deselected`。
- 失败原因：Oracle Gateway 不接受 `schema_owner`，工厂不传递该配置，也没有 owner 级元数据读取或会话切换。

## GREEN

| 保证 | 测试/命令 | 类型 | 结果 |
|---|---|---|---|
| 工厂把数据源级 `schema_owner` 传给 Oracle Gateway | `test_factory_passes_oracle_schema_owner_to_gateway` | 单元 | PASS |
| `ALL_TAB_COLUMNS/ALL_CONSTRAINTS` 使用绑定 owner，返回白名单内真实 FK | `test_oracle_schema_owner_reads_all_metadata_and_switches_query_schema` | 集成契约 | PASS |
| 查询前切换当前 Schema，并继续设置只读事务 | 同上 | 集成契约 | PASS |
| 注入式、带点号、带引号和空 owner 在连接前拒绝 | `test_oracle_schema_owner_rejects_unsafe_identifiers` | 安全单元 | PASS |
| 真实 Oracle 必须返回至少一条允许范围内的 FK | `test_remote_schema_details[oracle]` | 真实数据库 | PASS |

验证结果：

- `tests/test_remote_gateways.py`：`88 passed`。
- `tests/test_connector_acceptance.py`：`7 passed`。
- 相关回归：`115 passed`；完整后端：`963 passed, 32 skipped, 1 warning`。
- `scripts/connector_acceptance.py --connector oracle`：`8_passed`、退出码 `0`、`external_validation=passed`。
- 真实关系：`SALES.CUSTOMER_ID → CUSTOMERS.ID`。
- 当前虚拟环境未安装 `coverage`/`pytest-cov`，因此本批没有伪造覆盖率百分比；相关功能、错误路径、
  工厂传递和真实数据库链路均已由上述定向测试覆盖。

## 边界

- 本文完成时 Oracle `cost_estimate` 尚未支持；该边界已由后续
  `docs/testing/oracle-cost-estimate.tdd.md` 取代。后续实现使用独立 planner，仍未给查询账号增加 PLAN_TABLE DML。
- 当前连接仍为本机回环 1522 Shared Server，不代表生产网络和高可用验收。
- SQL Server、ClickHouse 仍没有真实实例证据。
