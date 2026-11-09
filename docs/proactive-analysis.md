# 主动分析计划

主动分析用于按用户显式授权的频率重复发起数据分析任务。它不是 API 进程内的隐藏定时器，也不会绕过现有查询链路；每次运行都会重新验证当前账号、租户、数据源权限、查询安全、成本预算、审批和审计。

## 安全默认值

- 功能默认关闭，`PROACTIVE_ANALYSIS_ENABLED=false`。
- 新计划默认处于 `draft`，创建计划不等于授权执行。
- 默认 `require_approval=true`，每次运行进入现有人工审批链路。
- Runner 必须显式传入 `--execute`，否则返回 `E_PROACTIVE_EXECUTE_REQUIRED`。
- API 进程不创建定时线程；部署系统只能按经过批准的频率调用一次性 Runner。
- 单计划同时受最小间隔、单日执行次数、单日估算预算和租约约束。
- 账号失效、租户不匹配或数据源撤权时失败关闭；身份失效会暂停计划。
- 运行记录只保存计划、运行、任务、trace、状态、稳定错误码和估算成本，不保存 SQL、参数、结果行或 Prompt。

## 生命周期

```text
draft -> active -> paused -> active
   \        \         \
    +--------+---------+-> closed
```

- `draft`：只保存配置，不进入到期队列。
- `active`：可被一次性 Runner 认领。
- `paused`：不执行，可在功能开关开启时恢复。
- `closed`：终态，不能恢复。

启用和恢复仅在 `PROACTIVE_ANALYSIS_ENABLED=true` 时允许。关闭功能后仍可创建草稿和查看历史，但不能把计划放回执行队列。

## 配置

```dotenv
PROACTIVE_ANALYSIS_ENABLED=false
PROACTIVE_ANALYSIS_MIN_INTERVAL_MINUTES=15
PROACTIVE_ANALYSIS_MAX_RUNS_PER_DAY=24
PROACTIVE_ANALYSIS_MAX_DAILY_BUDGET_USD=2.0
PROACTIVE_ANALYSIS_LEASE_SECONDS=120
```

计划自身的频率、次数和预算不能突破这些服务端上限。预算估算失败只影响当前计划，记录 `E_PROACTIVE_COST_ESTIMATE` 后继续扫描其他计划。

## 使用流程

1. 登录后进入“主动分析”。
2. 填写分析问题，选择当前账号有权访问的数据源。
3. 设置间隔、每日次数、每日预算，并保留默认人工审批。
4. 创建后计划为草稿；人工检查无误后点击“启用”。
5. 由外部调度系统按批准频率调用一次性 Runner。
6. 在页面查看运行记录，必要时暂停、恢复或永久关闭计划。

Runner 命令必须在项目根目录执行：

```cmd
.venv\Scripts\python.exe scripts\proactive_analysis_runner.py --execute
```

该命令只扫描一次并退出。计划调度应使用企业已有的任务平台、Windows Task Scheduler、Kubernetes CronJob 或等价系统；不要在 API Worker 内嵌循环。

## API

```text
GET  /api/v1/proactive-analysis/status
GET  /api/v1/proactive-analysis/plans
POST /api/v1/proactive-analysis/plans
POST /api/v1/proactive-analysis/plans/{id}/activate
POST /api/v1/proactive-analysis/plans/{id}/pause
POST /api/v1/proactive-analysis/plans/{id}/resume
POST /api/v1/proactive-analysis/plans/{id}/close
GET  /api/v1/proactive-analysis/plans/{id}/runs
```

所有接口都使用现有查询权限依赖，并按 `tenant_id + owner_id` 隔离。计划响应不会返回租约令牌；跨账号读取运行记录返回不存在，避免泄露资源是否存在。

创建示例：

```json
{
  "question": "每小时检查区域销售额是否出现异常下降",
  "datasource_id": "mysql-main",
  "interval_minutes": 60,
  "max_runs_per_day": 6,
  "daily_budget_usd": 0.5,
  "require_approval": true
}
```

## 并发与失败处理

- MySQL 使用 lease token 和过期时间原子认领到期计划。
- 同一时刻多个 Runner 竞争时，只有成功更新租约的实例获得执行权。
- 租约过期后计划可被重新认领；旧 token 不能完成新认领。
- 失效 Runner 的结果记录为 `E_PROACTIVE_LEASE_STALE`，不计为成功。
- 查询失败仍计入当日执行次数和估算成本，防止失败重试无限消耗。
- 预算或次数达到上限时不调用查询工作流，并把下次时间推迟到下一 UTC 日或正常间隔中更晚的时间。
- 主动运行使用稳定的 `client_request_id`，继续复用 DUR-01 的请求幂等准入，降低租约接管时重复创建任务的风险。

## 当前身份支持

- 关闭认证的本地模式支持 `local-dev`。
- MySQL 持久化认证支持数字用户 ID，并在每次运行时读取当前用户状态和租户。
- 非数字 API Key subject 当前返回 `E_PROACTIVE_IDENTITY_UNSUPPORTED`，不会静默降级为其他身份。

## 当前边界

本能力已完成本地代码、自动化测试、单实例 MySQL 迁移与仓储冒烟、前端浏览器流程。尚未完成企业调度平台、多机 Runner、MySQL/Redis 高可用、网络分区、真实告警通知、生产容量和真实业务准确率验收。`DUR-07 100%` 只表示本地开发清单完成，不表示生产上线完成。
