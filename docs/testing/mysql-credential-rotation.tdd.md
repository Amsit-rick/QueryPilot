# MySQL 本地凭据轮换 TDD 证据

## 范围

本专项处理旧 MySQL 数据卷仍复用 root、控制面和查询密码的问题。目标是轮换三类账号、原子更新
Git 忽略的 `.env`，并在数据库修改、验证或环境文件写入失败时执行补偿回滚。工具只面向本地开发
实例，不替代生产 Vault/KMS、DBA 审批或集中凭据轮换平台。

## 用户旅程

- 作为本地维护人员，我希望在不删除容器和数据卷的前提下拆分三类密码。
- 作为维护人员，我希望任何中途失败都不留下数据库与 `.env` 不一致的半完成状态。
- 作为安全审计人员，我希望 stdout、stderr、JSON 报告和异常都不包含数据库密码。
- 作为恢复负责人，我希望正式轮换前必须提供通过 SHA-256 校验的本地备份清单。

## RED 证据

首次执行：

```cmd
.venv\Scripts\python.exe -m pytest tests\test_rotate_local_mysql_credentials.py -q
```

结果：测试收集阶段因 `scripts.rotate_local_mysql_credentials` 尚不存在而失败。新增备份门禁时再次先
执行同一命令，因 `verify_backup_evidence` 尚不存在而失败。两次 RED 均由目标实现缺失触发，不是依赖
或测试环境故障。

## GREEN 证据

```cmd
.venv\Scripts\python.exe -m pytest tests\test_rotate_local_mysql_credentials.py -q
```

结果：`13 passed`。

联合回归：

```cmd
.venv\Scripts\python.exe -m pytest tests\test_rotate_local_mysql_credentials.py tests\test_local_setup.py tests\test_query_credentials.py tests\test_local_start_scripts.py tests\test_ci_verify.py -q
```

结果：`46 passed`。完整后端回归为 `980 passed, 33 skipped, 1 warning`。

## 保证矩阵

| # | 保证 | 测试类型 | 结果 |
|---|---|---|---|
| 1 | 生成三套互不相同且长度不少于 32 字符的密码 | 单元 | PASS |
| 2 | 旧 `.env` 缺 root 密码时只在读取阶段兼容使用控制面旧密码 | 单元 | PASS |
| 3 | 按查询、控制面、root 顺序轮换，全部验证后才原子更新 `.env` | 单元 | PASS |
| 4 | 数据库中途失败时反序回滚已完成账号且不修改 `.env` | 单元 | PASS |
| 5 | `.env` 写入失败时使用新凭据反序恢复数据库旧密码 | 单元 | PASS |
| 6 | dry-run 不修改数据库或环境文件 | 单元 | PASS |
| 7 | 用户名只允许受控标识符，拒绝注入片段、空格和 `@host` | 安全单元 | PASS |
| 8 | 报告、错误和标准输出不包含旧密码或新密码 | 安全单元 | PASS |
| 9 | 正式 CLI 轮换必须使用本地有效备份清单，拒绝路径穿越 | 单元 | PASS |
| 10 | 真实查询、控制面、root 新凭据均可连接，查询账号写入被拒绝 | 本地集成 | PASS |

## 真实本机证据

- 原容器 `data_query_agent-mysql-1` 和数据卷 `data_query_agent_mysql_data` 被复用，没有删除或重建。
- 轮换前创建新的时间戳备份，结构校验和 SHA-256 清单验证通过。
- 正式轮换报告为 `artifacts/mysql-credential-rotation.json`，仅记录类别、动作、状态和时间。
- `docker compose -f docker-compose.mysql.yml config --quiet` 通过。
- 三类新凭据真实连接通过；查询账号 `SELECT` 通过，`UPDATE ... WHERE 1=0` 被 MySQL 权限拒绝。

备份与轮换报告位于 Git 忽略目录，不进入仓库。文档不记录任何密码、Token 或连接字符串。

## 已知边界

- 多账号变更通过补偿事务实现，不是跨账号数据库事务；回滚失败会显式报告 `rollback_failed`，必须人工恢复。
- 本地备份校验只证明文件完整性，不能替代隔离实例恢复演练和业务抽样。
- 本专项不实现生产 Vault/KMS、双人审批、自动过期、集中审计或 DBA 托管轮换。
- 本轮未创建 Git checkpoint commit，因为工作区包含大量用户未提交改动且用户未要求提交。
