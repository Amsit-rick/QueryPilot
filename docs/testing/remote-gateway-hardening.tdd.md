# 远程 Gateway 生产化增强 TDD 证据

日期：2026-11-15

## 范围

按顺序完成 SQL Server `SHOWPLAN_XML`、ClickHouse `EXPLAIN ESTIMATE` 与资源限制、三库主动取消、
进程内有界连接池、成熟度元数据、深度 Schema、多 Schema / Database 以及 TLS 和稳定错误分类。

## RED / GREEN

- 连接池初始 RED：`ModuleNotFoundError: app.gateways.connection_pool`；GREEN 为 `10 passed`，覆盖复用、
  rollback、池耗尽、异常淘汰、空闲 TTL、跨 Gateway 实例共享和关闭。
- 成熟度元数据 RED：13 条旧硬编码断言失败；GREEN 后使用
  `accepted_local / not_validated / capability_gaps`，不声明生产通过。
- 深度 Schema RED：6 条类型/主键/索引/注释与 ClickHouse 详细元数据测试失败；GREEN 后远程 Gateway
  统一返回列类型、可空性、注释、主键、索引列和表注释，字段注释参与大 Schema 召回。
- 多命名空间 RED：限定名被旧安全规则全部拒绝；GREEN 后仅放行显式 `namespaces` + 限定名表白名单，
  未授权限定名与三段式跨数据库访问继续拒绝。
- 错误分类 RED：驱动异常模块不存在；GREEN 后稳定区分 timeout、auth、permission、connection、cancelled，
  对外消息不含原始主机、密码或供应商异常。

## 验证

- 相关 Gateway、安全、Schema、数据源与验收回归：`270 passed`。
- 首轮全量：`1210 passed, 35 skipped, 2 failed`；失败来自旧安全语料仍要求所有限定名无条件拒绝。
- 修正为“授权两段式限定名允许、未授权限定名仍按只读边界拒绝”后，安全专项 `56 passed`。
- 最终后端全量：`1212 passed, 35 skipped, 1 warning`；`compileall`、`pip check` 与差异空白检查通过。
- SQL Server 与 ClickHouse 的新成本门禁已分别在本机真实实例取得 `9_passed` 报告。

## 边界

本轮多 Schema / Database、TLS 证书链和错误分类主要由本地契约与驱动参数测试覆盖，尚未在企业多租户
Schema、真实 CA/mTLS、网络分区和生产连接上验收。进程内连接池在多 Worker 部署时总连接数仍为
Worker 数乘以池大小，需要结合数据库上限和部署副本数配置。
