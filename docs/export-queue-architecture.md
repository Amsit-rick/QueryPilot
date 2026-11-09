# 导出任务队列架构

## 目标

将导出 CSV 从 FastAPI 进程内后台任务迁移到可横向扩展的 worker，同时保留 MySQL `export_jobs` 作为任务状态和权限事实源。

## 运行模式

- `EXPORT_EXECUTION_MODE=inline`：当前默认模式，使用 FastAPI BackgroundTasks，适合本地开发。
- `EXPORT_EXECUTION_MODE=redis`：生产模式，API 写入 Redis 队列，worker 领取任务并更新 MySQL。
- Redis 不可用时，API 不自动静默降级到 inline，避免重复执行；仅在显式配置 `inline` 时使用进程内执行。

## 任务生命周期

`queued -> running -> completed`

失败时进入 `failed`，worker 对网络或临时数据库错误最多重试 3 次；超过次数保留错误信息并释放临时文件。

## 安全边界

worker 只读取 `export_jobs` 中的 SQL 和参数，重新执行只读校验；下载接口仍按 `tenant_id/user_id` 校验，不信任 Redis 消息中的权限字段。

## 实施顺序

1. 增加执行模式配置和 Redis 连接配置。
2. 新增 Redis producer/consumer 与 worker 命令。
3. API 在 redis 模式下只入队，不启动 BackgroundTasks。
4. 增加重试、超时、幂等和队列深度指标。
5. 增加 Docker Compose、集成测试和回滚说明。
