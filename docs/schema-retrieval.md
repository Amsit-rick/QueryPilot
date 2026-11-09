# 千表级 Schema 召回与关系路径规划

## 目标与边界

当授权 Schema 超过配置阈值时，Query Pilot 在模型生成 SQL 前选择与当前问题相关的表和字段，
并按已声明关系补齐 JOIN 桥接表。召回只减少模型上下文；完整授权 Schema 继续承担最终 SQL 校验边界。

执行顺序：

1. 数据源读取 Schema、主键和外键；人工关系由语义目录补充。
2. RBAC、表级和字段级策略先生成完整授权 Schema。
3. 对授权表/字段文档运行 BM25；语义条目和权限内字段值词典继续作为强种子。
4. 若部署注册了 Embedding scorer，则与启发式、BM25 通过 RRF 融合；调用失败时带稳定错误码降级，启动和查询不失败。
5. 关系图在最大跳数内寻找最短路径，补齐桥接表和必要主外键字段。
6. 模型接收精简 Schema、范围内关系、规划路径和歧义路径。
7. 模型 SQL 仍针对完整授权 Schema 执行 AST、权限、成本和只读校验。

## 受控语义扩展与字段值词典

管理员可以在语义目录中创建 `value_dictionary`，将用户说法映射为数据库实际保存的低基数标准值。
例如把“华东”“东区”映射为 `sales.region=EAST`。词典定义必须包含目标表、目标字段、授权引用和
1 到 200 个标准值；每个值最多配置 20 个别名。

查询时目录、普通语义和字段值独立召回，再合并为模型上下文。字段值只有在当前租户、数据源以及
完整授权 Schema 都允许目标字段时才会返回；模型只收到实际命中的标准值，不会收到整个词典。
该实现不会在在线查询路径执行 `SELECT DISTINCT`，避免全表扫描、高基数字段膨胀和未授权值泄露。

当前扩展模式为管理员维护的 `curated` 受控扩展，不是让 LLM 自由改写业务问题。数据库后端、方言、
可获取版本以及服务器当前日期会一并进入生成上下文；版本不可获取时明确使用 `unknown`。

## 配置

| 环境变量 | 默认值 | 含义 |
| --- | ---: | --- |
| `SCHEMA_RETRIEVAL_ENABLED` | `true` | 是否启用大 Schema 召回 |
| `SCHEMA_RETRIEVAL_TABLE_THRESHOLD` | `50` | 超过该表数才缩减 Schema |
| `SCHEMA_RETRIEVAL_MAX_TABLES` | `20` | 模型上下文最大表数，包含桥接表 |
| `SCHEMA_RETRIEVAL_MAX_COLUMNS_PER_TABLE` | `40` | 每表普通字段上限；必要主外键字段优先保留 |
| `SCHEMA_RETRIEVAL_MAX_HOPS` | `3` | JOIN 路径最大跳数 |
| `SCHEMA_EMBEDDING_PROVIDER` | `disabled` | `disabled` 或 OpenAI 兼容的 `openai_compatible` |
| `SCHEMA_EMBEDDING_BASE_URL` | 空 | Embedding API 基础地址，不允许 URL 内凭据、查询参数或片段 |
| `SCHEMA_EMBEDDING_MODEL` | 空 | Embedding 模型标识 |
| `SCHEMA_EMBEDDING_API_KEY_ENV` | `SCHEMA_EMBEDDING_API_KEY` | 仅保存密钥环境变量名，不保存密钥值 |
| `SCHEMA_EMBEDDING_BATCH_SIZE` | `64` | 文档向量批大小 |
| `SCHEMA_EMBEDDING_CACHE_ENTRIES` | `10000` | 每进程文档向量 LRU 上限 |
| `SCHEMA_EMBEDDING_MAX_DOCUMENTS` | `5000` | 单次允许评分的最大授权表文档数 |
| `SCHEMA_EMBEDDING_MAX_TOTAL_CHARS` | `2000000` | 单次授权 Schema 文档字符总预算 |
| `SCHEMA_EMBEDDING_CACHE_BACKEND` | `memory` | `memory` 或跨 Worker 的 `redis` |
| `SCHEMA_EMBEDDING_CACHE_TTL_SECONDS` | `86400` | Redis 文档向量缓存 TTL |
| `SCHEMA_EMBEDDING_CIRCUIT_BACKEND` | `memory` | Embedding 熔断状态使用 `memory` 或 `redis` |
| `SCHEMA_EMBEDDING_CIRCUIT_FAILURES` | `3` | 连续失败打开熔断的阈值 |
| `SCHEMA_EMBEDDING_CIRCUIT_RESET_SECONDS` | `30` | 熔断恢复等待时间 |
| `SCHEMA_EMBEDDING_REMOTE_ALLOWED` | `false` | 非回环远程服务的 Schema 元数据外发确认开关 |

## 歧义处理

关系图按无向方式导航，但输出关系保留数据库声明的方向。存在多条等长最短路径时，系统将替代路径写入
`ambiguous_paths`，并优先选择路径上最低关系置信度更高的方案。若候选 SQL 同时使用该表对，查询进入人工审批，原因码为 `ambiguous_join_path`。
数据库外键只能证明结构约束，人工关系只有在已审批且置信度达标时才可免于关系确认门禁。

`retrieval_stats.table_scores` 会分别记录语义种子、字段值、表名/别名/描述、业务域、元数据质量、历史成功次数、BM25、Embedding 和 RRF 得分。`source_rankings` 分开保留 heuristic、BM25、Embedding、融合候选和关系图补齐后的排序，评测可分别计算 Recall@K、MRR、融合增益和图增益。历史成功只作为小权重排序信号，不能覆盖权限过滤，也不能把旧错误自动强化为业务真值。

Embedding 是可插拔能力，默认不下载模型、不访问外网。除了通过
`app.schema_embedding_runtime.set_schema_embedding_scorer()` 注入自定义 scorer，也可配置 OpenAI 兼容
`/embeddings` 服务。系统只在 RBAC、租户、数据源和字段过滤完成后构造文档；远程地址必须使用 HTTPS，
并显式启用 `SCHEMA_EMBEDDING_REMOTE_ALLOWED=true`。密钥只从 `SCHEMA_EMBEDDING_API_KEY_ENV` 指向的环境变量读取。

文档向量按模型和内容哈希缓存在进程内有界 LRU 中，用户问题向量不缓存。响应维度、索引、数值有限性、
文档数量和单文档长度均受限；上游异常只保留稳定错误码。未配置时运行模式为 `bm25_graph`，成功时为
`hybrid`，异常时标记 `E_EMBEDDING_UNAVAILABLE` 并安全降级。`/health` 只返回配置状态、稳定错误码和缓存计数，
不返回服务地址、模型密钥或 Schema 内容。

多 Worker 可把文档向量缓存和熔断状态切换到 Redis。共享缓存键只包含服务命名空间哈希和文档内容哈希，
值只保存有限浮点向量，不保存原始表名、字段名、问题或 Schema 文本。Redis 缓存配置为必需模式时，缓存
不可用会在请求向量服务前失败并降级，避免静默退回进程缓存导致每个 Worker 重复计费。熔断器按服务键隔离，
不会因为一个 Embedding 服务失败而误伤 SQL 生成模型或其他提供商。

当前配置的外部服务可用以下命令生成脱敏验收报告：

```cmd
.venv\Scripts\python.exe scripts\schema_embedding_acceptance.py --output artifacts\schema-embedding-acceptance.json --require-external
```

脚本只发送两条固定合成文档，报告不保存分数、响应正文或密钥。未配置时明确 `external_validation=not_run`；
协议探针通过也只能证明当前服务契约可用，不能替代企业 Schema 的召回准确率、数据出境和供应商审批。

## 本地评测

在项目根目录的 Windows CMD 中运行：

```cmd
.venv\Scripts\python.exe scripts\evaluate_schema_retrieval.py --iterations 10 --dataset data\schema_retrieval_eval.json --output artifacts\schema-retrieval-eval.json
```

报告包含版本、强制阈值、表/字段召回、JOIN 路径准确率、各召回源 Recall@20/MRR、融合与图增益、Embedding 降级状态、歧义识别、越权元数据泄漏、P50/P95 延迟和 Schema 缩减率；未达阈值时脚本返回非零退出码并阻断 CI。
该评测使用固定合成 Schema，不调用模型或真实数据库，不能替代真实业务查询集验收。

## 真实数据库验收

真实验收至少需要提供：表/字段中文名和别名、数据库外键、已审批软关系、业务指标口径、软删除规则、
快照时间规则，以及包含单表、两跳、三跳、多路径和双一对多聚合的标准问题集。应分别统计召回错误、
SQL 结构错误和业务口径错误，不能只以 SQL 可执行作为准确。
