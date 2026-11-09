# DUR-01 查询请求幂等准入 TDD 证据

日期：2026-11-07

## 1. 目标与用户路径

- 用户或代理在网络重试时复用同一 `client_request_id`，系统返回原 `task_id`，不重复执行 SQL。
- 同一租户、用户、操作和请求 ID 对应不同请求内容时，系统返回 `E_IDEMPOTENCY_CONFLICT`。
- 多个并发副本同时收到相同请求时，只有一个请求获得执行权。
- 追问重放不重复开启轮次，不重复创建 TaskStep、TaskEvent、TaskArtifact 或审批副作用。

## 2. RED 证据

命令：

```cmd
.venv\Scripts\python.exe -m pytest tests\test_query_request_admission.py -q
```

首次结果：测试收集失败，`ModuleNotFoundError: No module named 'app.domain.query_admission'`。失败原因是目标准入领域与服务尚不存在，不是测试环境或依赖错误。

## 3. GREEN 证据

| 保证 | 测试类型 | 结果 |
|---|---|---|
| 首次请求获得执行权，重复请求复用同一 task_id | 单元 | PASS |
| 相同 ID、不同请求指纹返回冲突 | 单元/API | PASS |
| 租户和用户作用域相互隔离 | 单元 | PASS |
| 10 个并发内存准入只有 1 个执行者 | 并发单元 | PASS |
| 两个并发 API 应用调用只进入一次查询工作流 | 应用并发 | PASS |
| 准入记录不保存 SQL、参数值或结果行 | 单元 | PASS |
| 完成与失败状态可持久更新 | 单元 | PASS |
| 非法 client_request_id 被请求模型拒绝 | 契约 | PASS |
| API 重放不增加步骤、事件或产物 | API 集成 | PASS |
| follow-up 重放不新增对话轮次 | API 集成 | PASS |
| MySQL 10 个并发请求只有 1 个执行者和 1 条记录 | 真实 MySQL 冒烟 | PASS |

实际命令与结果：

```text
.venv\Scripts\python.exe -m pytest tests\test_query_request_admission.py -q
15 passed in 2.81s

.venv\Scripts\python.exe -m pytest tests\test_query_request_admission.py tests\test_query_application_service.py tests\test_query_streaming.py tests\test_layered_services.py tests\test_api.py tests\test_mysql_migrate.py -q
85 passed in 12.40s

.venv\Scripts\python.exe -m pytest -q
995 passed, 33 skipped, 1 warning in 43.23s

frontend\npm run build
80 modules transformed; built successfully

真实 MySQL 并发冒烟
claims=10 execution_owners=1 stored_rows=1
```

V024 已通过迁移器应用到本机 MySQL。迁移器本次同时发现并应用了此前未登记的 V023；没有删除或重建现有数据卷。

## 4. 实现边界

- `client_request_id` 为兼容旧客户端暂时可选；当前 Vue 查询和追问入口都会生成 UUID 并发送。
- 请求指纹使用规范化 JSON 的 SHA-256，准入表不保存问题正文、SQL、参数值或结果行。
- 完成请求从 DataTask 和 TaskArtifact 投影重放。现有表格资产保存有界结果预览，因此重放保证 `row_count`、SQL、列、摘要和持久化预览一致，不承诺进程重启后恢复首次响应中未持久化的全部行。
- 正在执行的重复请求返回同一 `task_id` 和 `status=running`，不会等待或启动第二次执行。
- 失败请求返回稳定错误码；如需重新执行，客户端必须生成新的 `client_request_id`。
- 当前未安装 `pytest-cov`，因此未声明覆盖率百分比；覆盖证据来自上述单元、并发、API 集成、完整回归和真实 MySQL 冒烟。

