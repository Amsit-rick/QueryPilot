# CI 安全与回归门禁

对应任务 **REL-02**。目标是让合并前在本地和 CI 跑同一套检查，而不是等推送后才发现失败。

## 1. 本地执行

```cmd
cd /d D:\QueryPilot
.venv\Scripts\python.exe scripts\ci_verify.py --report artifacts/ci-report.json
```

常用参数：

| 参数 | 作用 |
|---|---|
| `--skip-tests` | 只做静态检查，最快 |
| `--skip-frontend` | 跳过 `npm run build` |
| `--skip-e2e` | 跳过 Playwright |
| `--skip-audit` | 跳过依赖漏洞扫描（仅适用于离线快速静态检查） |
| `--require-audit` | 将依赖漏洞扫描设为发布阻塞项；工具缺失或扫描未完成也失败 |
| `--report <路径>` | 输出 JSON 报告，便于留档 |

退出码非 0 表示门禁未通过，可用于 Git Hook 或流水线判断。

## 2. 门禁项

| 门禁 | 阻塞合并 | 说明 |
|---|:---:|---|
| 迁移静态检查 | 是 | 版本号在同方言内唯一；禁止 `DROP DATABASE`、`DROP SCHEMA`、`TRUNCATE`、`GRANT ALL` |
| 密钥扫描 | 是 | `.env` 和私钥文件不得被 Git 跟踪；疑似硬编码凭据仅告警 |
| 供应链与容器静态检查 | 是 | 前端依赖必须精确版本并与 lockfile 一致；应用镜像必须声明非 root 用户；Redis 必须有健康检查且 API/worker 等待 `service_healthy` |
| 依赖漏洞扫描 | 发现漏洞时是 | 使用可用的 `pip-audit` 与 `npm audit`；工具缺失或外网不可用时明确记录未执行，不伪报无漏洞 |
| 后端测试 | 是 | `pytest -q` 全量 |
| 高风险回归 | 是 | 认证、导出队列、SQL 护栏、权限策略、API 契约 |
| 前端构建 | 是 | `npm run build` |
| E2E | 集成环境 | Playwright 不可用时自动跳过并明确标注 |

## 3. 设计取舍

### 迁移版本按方言分组

`migrations/mysql/V001` 与 `migrations/oracle/V001` 属于两条独立演进历史，**跨方言不算版本冲突**。校验只在同方言目录内做唯一性检查。

### dba 目录豁免命名规则

`migrations/mysql/dba/` 下的脚本由 DBA 手工执行，不参与应用自动迁移，因此不强制 `Vxxx__` 命名，仅记录警告。

### 供应链与容器静态检查

门禁拒绝 `latest`、`*`、范围版本或其他无法复现的前端直接依赖，并检查
`package.json` 与 `package-lock.json` 的根依赖版本一致。Dockerfile 必须在最终运行阶段
使用非 root 用户；生产 Compose 中 Redis 必须提供健康检查，API 和导出 worker 必须等待
`service_healthy`，避免 Redis 尚未就绪时误判应用可接收流量。

基础镜像 digest 当前仅告警，因为 digest 需要由发布环境维护并定期更新；生产上线前仍应
由镜像供应链流程将基础镜像固定到经过批准的 digest。

### 密钥扫描区分代码标识符与真实凭据

`password="changePassword"` 这类 Vue 属性是函数引用，不是密钥。规则：纯字母驼峰且不含数字的值视为代码标识符跳过；含数字或符号的值才会告警。

告警不阻断合并。阻断只发生在确凿情况：`.env` 被跟踪、私钥文件被跟踪。

### 测试结果摘要抗噪声

某些环境会在 pytest 清理临时目录时追加无关 traceback。门禁从输出中匹配包含 `passed`/`failed`/`error` 的摘要行，而不是取最后一行，避免把环境噪声误报成测试结果。

## 4. CI 工作流

`.github/workflows/ci.yml` 拆分为四个 Job：

```text
gates      静态检查 + 后端测试 + 高风险回归（阻塞）
frontend   npm ci + npm run build（阻塞）
e2e        启动 SQLite 模式 API + Playwright（集成环境阻塞）
```

后端 Job 会上传 `artifacts/ci-report.json` 作为可保存的测试报告。
同一 Job 还会归档迁移计划、CycloneDX SBOM、许可证盘点、镜像清单和
`release-evidence.json`。发布证据 v2 会校验专项报告的格式和计数一致性：
`local_evidence_complete` 表示所提供本地证据是否完整有效，`production_evidence_ready`
表示这些证据是否仍包含目标库未核对、许可证未审批、镜像未锁定/扫描/签名/审批等阻塞。
日常 CI 默认只要求前者；正式发布调用证据工具时必须增加 `--require-production-evidence`。

如需迁移到 Gitee Pipelines，直接复用 `scripts/ci_verify.py`，只需替换工作流文件，检查逻辑无需改动。

## 5. 当前限制

- E2E 需要后端 API 在 `127.0.0.1:8000` 提前可用；CI 中以 SQLite 模式启动，不覆盖 MySQL 专属链路。
- 依赖漏洞扫描是可选执行门禁：当前本地 Python `pip-audit` 已执行并报告无已知漏洞，
  但 `npm audit` 仍依赖 registry 审计接口；工具缺失或网络失败会在报告中标为“未执行/未完成”
  并告警，不代表依赖安全。发布前仍必须在有网络的 CI 环境保存两类原始报告。
- 发布流水线应使用 `--require-audit`；本地离线开发可以省略该参数，但报告中的“未执行”不能
  被解释为安全通过。
- JSON 报告中的每个门禁包含 `executed` 和 `blocking`：`executed=false` 表示工具或集成环境
  不可用，`blocking=false` 表示该项仅告警；发布平台应优先读取 `blocking_failures` 和
  `not_executed`，不要只根据总通过数判断生产验收。
- 生产 Compose 的 API 和导出 worker 还必须启用 `no-new-privileges`、drop 全部 capabilities、
  `init`、PID 上限和内存上限，避免单个容器异常无限消耗宿主机资源。
- SBOM 由 `scripts/generate_sbom.py` 生成 CycloneDX 1.5 JSON，覆盖当前 Python 环境和前端
  lockfile；报告只证明组件清单已生成，不等同于漏洞扫描或许可证合规结论。
- 备份、恢复和企业数据导入脚本必须由调用环境显式提供 `MYSQL_ROOT_PASSWORD` 或
  `MYSQL_PASSWORD`，门禁会拒绝这些运行脚本中的密码默认值。本地 MySQL Compose 同样必须固定
  镜像 digest，并通过 `MYSQL_PASSWORD`、`MYSQL_ROOT_PASSWORD`、`MYSQL_QUERY_PASSWORD` 注入密码，
  不再允许仓库内固定演示数据库凭据。
- MySQL 备份/恢复命令不得把密码拼进进程参数，脚本通过 `MYSQL_PWD` 环境变量传给容器内
  客户端；备份文件名使用 UTC 时间戳，失败时会删除可能产生的半成品。
- 发布前可运行 `scripts\mysql_migrate.py --dry-run` 查看有序迁移计划；该预检不连接数据库，
  不能替代目标环境的增量迁移和回滚演练。
- 查询入口的 Gateway Schema/连接异常也纳入稳定错误契约：客户端收到 `502` 与脱敏的
  `E_GATEWAY_*` 错误，而不是底层驱动堆栈或未处理的 500。
- 数据网关注册配置本身无效时返回 `500/E_GATEWAY_CONFIG`；这属于部署配置错误，不能被
  客户端重试掩盖。未知网关、表和权限问题分别保持 `404`、`404` 和 `403`。
- `docker-compose.app.yml` 的数据库密码必须通过环境变量注入；CI 不应把 `.env` 或真实凭据上传为构建产物。
- 本报告只描述门禁规则本身，不构成生产验收证据。
