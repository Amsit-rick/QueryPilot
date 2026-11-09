# LangChain 与 LangGraph 选型

## 结论

本项目采用 **LangGraph 主流程 + LangChain 可选模型适配**。

## 为什么不是只用 LangChain

查询不是一次工具调用，而是一个带安全边界的状态流程：

`生成 SQL → 校验 → 权限过滤 → 执行 / 审批 → 结果审计`

其中包含有限重试、人工审批、并发保护、Redis checkpoint 和失败恢复。此类有条件分支与持久化流程由 LangGraph 管理更清晰。

## LangChain 在哪里使用

当 `MODEL_PROVIDER=langchain` 时，LangChain 负责：

- Prompt 模板
- OpenAI-compatible Chat Model（兼容 DeepSeek）
- 输出解析

无论模型适配器如何切换，SQL 都必须经过现有 `validate_sql`、数据源授权、行级策略和只读执行。

## 配置建议

- 日常开发或离线环境：`MODEL_PROVIDER=direct`
- 已安装 `langchain-openai` 并需要统一模型观测：`MODEL_PROVIDER=langchain`
- 流程运行时：生产建议 `AGENT_RUNTIME=langgraph`
- 只有在明确选择 `AGENT_RUNTIME=bounded` 时，才使用兼容性 Graph

LangChain 不是安全边界，LangGraph 也不替代数据库权限系统；两者都不能绕过项目已有的确定性护栏。
