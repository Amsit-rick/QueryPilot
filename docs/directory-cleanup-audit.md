# 项目目录清理审计

审计日期：2026-10-07。

## 已确认并清理

| 路径或文件 | 判断依据 | 处理 |
| --- | --- | --- |
| `.codex_tmp/` | 接口文档渲染页、联系表、旧 OpenAPI 副本和临时生成脚本，不参与产品运行或打包 | 删除 |
| `.pytest_cache/`、项目源码下的 `__pycache__/` | Python/Pytest 可再生缓存 | 删除 |
| `test-results/`、`frontend/test-results/` | 旧 Playwright 失败上下文和运行状态 | 删除 |
| `frontend/dist/` | `npm run build` 可重新生成 | 删除 |
| `data/exports/` | 本地测试和查询生成的 392 个临时 CSV，已被交付脚本排除 | 删除 |
| 根目录 `package-lock.json` | 内容为空，根目录没有 `package.json`；实际前端依赖锁位于 `frontend/package-lock.json` | 删除，并从测试包白名单移除 |

`.gitignore` 已补充本地工具目录、证据产物、根级测试结果、覆盖率和 Python 字节码规则。
开发迁移包也明确排除 `.codex_tmp/` 和 `.workbuddy/`，避免把本机工具数据交付给其他电脑。
测试包数据白名单同步补充关系准确率和 SQL 安全评测数据，避免整理后出现“测试代码存在、评测数据缺失”。

## 必须保留

| 路径 | 原因 |
| --- | --- |
| `.venv/` | 当前本地 Python 开发环境；虽然可重建，但删除会立即影响开发和测试 |
| `frontend/node_modules/` | 当前前端开发依赖；交付包已排除 |
| `data/demo.db` | SQLite 演示库，也是本地企业演示数据和迁移流程的数据来源 |
| `data/audit.jsonl` | 本地审计记录；应通过现有轮转机制管理，不作为普通缓存删除 |
| `artifacts/drills/` | 真实 MySQL 恢复演练 SQL、清单和对比证据，约 59MB |
| `app/`、`tests/` 中未跟踪源码 | 当前 SQL 安全、多轮对话、质量反馈、实验连接器和 Schema 召回实现，属于未提交成果 |

## 暂不删除的候选项

| 路径 | 当前判断 | 后续处理条件 |
| --- | --- | --- |
| `artifacts/` | 多批 CI、SBOM、许可证和验收报告；大部分体积很小，恢复演练备份占主要空间 | 在确认不再需要本地发布/恢复证据后，将整个目录归档到项目外，而不是逐个猜测删除 |
| `.workbuddy/` | 两份旧项目记忆，不属于产品代码 | 已忽略并从迁移包排除；确认不再使用 WorkBuddy 后可删除 |
| `data/audit.jsonl` | 约 4.8MB，属于运行证据而非源码 | 使用审计轮转/归档流程处理，不直接删除当前日志 |

## 目录约定

- 产品源码：`app/`、`frontend/src/`。
- 自动化测试：`tests/`、`frontend/e2e/`。
- 开发和运维脚本：`scripts/`、`deploy/`、`migrations/`。
- 产品文档：`docs/`；根目录只保留 README、交接、计划、日志和明确的用户交付文档。
- 运行数据和本机证据：`data/`、`artifacts/`，默认不进入 Git 或迁移包。
