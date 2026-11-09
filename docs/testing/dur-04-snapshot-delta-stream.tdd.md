# DUR-04 Snapshot + Delta Stream TDD 证据

日期：2026-11-07  
来源计划：`项目改造计划书.md` 的 Durable Agent `DUR-04`

## 用户旅程

1. 用户在查询流连接中断后，能够从最后已确认序号恢复展示，而不会再次执行查询。
2. 多 Worker 部署能够共享短期流状态；Redis 不可用时失败关闭。
3. 客户端能拒绝重复、倒序、未知或损坏的流事件，避免重复行和同步重放。
4. 旧 v1 客户端继续按原事件顺序工作。

## RED 证据

| 阶段 | 命令 | 实际失败 |
|---|---|---|
| v2 核心 | `.venv\Scripts\python.exe -m pytest tests\test_query_stream_resume.py -q` | `ModuleNotFoundError: app.repositories.memory_query_stream_repository` |
| Redis 仓储 | `.venv\Scripts\python.exe -m pytest tests\test_redis_query_stream_repository.py -q` | `ModuleNotFoundError: app.repositories.redis_query_stream_repository` |
| Redis 装配 | `.venv\Scripts\python.exe -m pytest tests\test_redis_query_stream_repository.py tests\test_startup_checks.py -q` | 缺少 `build_durable_query_stream_service`，启动检查未约束 Redis 查询流 |
| 前端 v2 | `npx playwright test e2e/query-streaming.spec.js` | v2 请求头缺失，2 条新增场景失败 |

首轮后端 GREEN 前还发现测试错误地假设快速查询绝不会出现 `progress`；断言改为允许可选心跳，同时继续验证业务事件顺序与严格单调序号。

## GREEN 证据

| # | 保证 | 测试/命令 | 类型 | 结果 |
|---|---|---|---|---|
| 1 | 首包快照、delta 序号单调、重连只读游标后事件且查询只执行一次 | `tests/test_query_stream_resume.py` | 单元/集成 | PASS |
| 2 | 客户端断开后生产者继续，完成态可由快照恢复 | `tests/test_query_stream_resume.py` | 异步集成 | PASS |
| 3 | 裁剪游标回退快照、超前游标 409、租户/用户隔离 | `tests/test_query_stream_resume.py` | API/安全 | PASS |
| 4 | Redis 原子认领、单调 CAS、事件上限、scope hash 和失败关闭 | `tests/test_redis_query_stream_repository.py` | 仓储契约 | PASS |
| 5 | v1 事件顺序和兼容行为不变 | `tests/test_query_streaming.py` | 回归 | PASS |
| 6 | v2 快照、断线续传、快照替换、重复序号失败关闭且不重放同步查询 | `frontend/e2e/query-streaming.spec.js` | Playwright E2E | `7 passed` |
| 7 | DUR-04 与准入、多轮对话、启动检查联合回归 | 定向 pytest 命令 | 集成 | `76 passed` |
| 8 | 全部后端行为无回归 | `.venv\Scripts\python.exe -m pytest -q` | 全量 | `1041 passed, 33 skipped, 1 warning` |
| 9 | 前端可生产构建 | `npm run build` | 构建 | PASS |
| 10 | Redis 5.0 Lua/TTL 基础契约可执行 | 隔离端口真实 Redis 冒烟 | 本机集成 | PASS |

## 静态与依赖检查

- `.venv\Scripts\python.exe -m compileall -q app scripts`：PASS。
- `.venv\Scripts\python.exe -m pip check`：`No broken requirements found.`
- `git diff --check`：PASS；输出仅为既有 Windows LF/CRLF 转换提示。

## 覆盖与已知缺口

- 当前环境未执行 `pytest-cov`，不声明覆盖率百分比。
- 33 个 skipped 主要是未配置目标数据库真实实例的探针，不计为通过。
- 查询仍是完整执行后再传输分块，未实现数据库游标级流式。
- Redis 状态有 TTL 和事件数量上限；游标过旧回退最新完整快照，不提供无限历史。
- 真实 Redis 只验证单实例。Sentinel/Cluster、网络分区、多 Worker 故障切换和进程崩溃后的后台查询延续仍需外部生产环境验收。
- 本轮没有创建 Git checkpoint commit，因为用户未授权提交；RED/GREEN 证据保存在本文件。
