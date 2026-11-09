# Query Pilot 集成验收记录

更新时间：2026-09-15

本文记录当前开发环境的可复核验收证据，不等同于企业生产验收。

## 已验证

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 后端全量测试 | 通过 | `316 passed, 7 skipped, 1 warning` |
| 前端生产构建 | 通过 | `npm run build` |
| Windows 本地门禁 | 通过 | `scripts/ci_verify.py --skip-e2e`，5/5 |
| Docker MySQL 健康 | 通过 | `data_query_agent-mysql-1` healthy，宿主机端口 3307 |
| V014 增量迁移 | 通过 | `schema_migrations` 已记录 V014，`users.display_name` 存在 |
| V014 重复执行 | 通过 | 第二次运行未重复执行或报错 |
| 真实认证资料链路 | 通过 | admin 登录、Bearer `/auth/me`、PATCH 资料、重新读取、恢复空值均成功 |

## 当前运行边界

- MySQL 验收针对本机 Docker 开发卷，不代表企业生产数据库。
- 资料链路使用开发账号，测试结束已将 `display_name` 恢复为空。
- 完整真实 Playwright E2E 仍需要浏览器依赖和有效测试账号；mock 场景通过不能替代真实生产验收。
- Oracle、企业 SSO/JWKS、正式密钥托管、HTTPS、监控告警、容量压测、备份恢复演练和多实例故障演练均未在本地完成。
- 角色权限策略目前写入本地 JSON；生产多实例应迁移到集中存储并增加版本审批与审计。

## 迁移注意事项

V014 使用标准 `ALTER TABLE ... ADD COLUMN`。MySQL 8.4 当前开发卷不接受 `ADD COLUMN IF NOT EXISTS`，项目迁移器会通过版本记录和既有 1060 错误兼容处理保证重复执行安全。
