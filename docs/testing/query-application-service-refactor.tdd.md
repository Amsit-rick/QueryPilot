# Query Application Service 拆分 TDD 证据

日期：2026-11-04

## 目标

将 `app/main.py` 中原 500 余行 `/api/v1/query` 路由编排迁移到独立应用服务，保持同步查询、NDJSON 流式查询、多轮追问、审批、权限、Agent 修复、审计和结果资产行为不变。

## RED

先新增 `tests/test_query_application_service.py::test_query_route_delegates_to_application_service`，要求路由通过 `get_query_application_service()` 委托执行。

首次有效失败：

```text
AttributeError: module 'app.main' has no attribute 'get_query_application_service'
1 failed
```

该失败证明旧实现仍由路由直接承担完整查询编排。

## GREEN

- 新增 `app/services/query_application_service.py`：承接原查询应用编排。
- 新增 `QueryApplicationDependencies`：保留模型生成、Agent Graph、结果分析、Skill 路由和用户数据授权五个可替换协作者，避免既有 route-level monkeypatch 测试因移动模块失效。
- `app/main.py::query` 缩减为两行定义和一行委托；`query_stream` 继续调用同一个 `query` 入口，不创建第二条安全链路。
- 新增流式复用测试，验证同步和流式入口调用同一应用服务。

结构结果：

```text
app/main.py                     964 lines
query()                         2 lines
QueryApplicationService.execute 514 lines
```

本批是行为保持型的路由/应用层拆分，不同时改写 SQL 生成、安全门禁或任务状态算法。应用服务内部后续仍可继续按请求上下文、任务协调、语义解析、审批协调和结果物化拆分，但不属于本次兼容性迁移的完成条件。

## 验证

```text
结构测试：2 passed
查询相关定向回归：136 passed
全量后端：802 passed, 32 skipped, 1 warning
Python 编译：通过
git diff --check：通过（仅既有 LF/CRLF 提示）
```

32 个 skipped 为未配置 Oracle、SQL Server、ClickHouse 真实实例的 live 探针，不计为通过，也不由本次重构解决。唯一 warning 为既有 Starlette TestClient Cookie API 弃用提示。
