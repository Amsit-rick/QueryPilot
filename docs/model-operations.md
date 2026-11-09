# 模型与提示词运营

模型运营页面位于“模型运营”，仅管理员可访问。租户配置优先于 `.env` 中的全局默认配置；没有可用租户默认提供商时，运行时继续使用 `MODEL_PROVIDER`、`MODEL_BASE_URL`、`MODEL_NAME` 和 `MODEL_API_KEY`。

## 密钥配置

提供商记录只保存环境变量名，例如 `TENANT_MODEL_API_KEY`，不保存密钥内容。先在 `.env` 或企业 Vault/KMS 的进程注入配置中加入：

```dotenv
TENANT_MODEL_API_KEY=实际密钥
```

重启 API 后，在管理页面填写：

- 接口地址：OpenAI 兼容 API 的基础地址，例如 `https://api.deepseek.com`
- 模型标识：例如 `deepseek-chat`
- 密钥环境变量：例如 `TENANT_MODEL_API_KEY`

页面和 API 只返回密钥是否已注入，不返回密钥值。健康检查由管理员手动触发，请求提供商的 `/models` 接口；系统只记录状态、错误码、检查时间和延迟，不保存响应正文。

## 提示词版本

`sql_generation` 是查询生成使用的租户模板。创建新版本时版本号自动递增，勾选“保存后激活”可直接切换；历史版本仍保留并可重新激活。

租户提示词不能替换系统只读约束。运行时始终追加只读单条 `SELECT`、Schema 白名单以及禁止文件、网络、事务和写操作的安全要求，生成后的 SQL 仍需经过现有 SQL Guardrail、权限和 Data Gateway。

## 回滚与降级

- 提示词异常：在版本列表激活上一个版本。
- 提供商异常：停用该提供商，或将另一个已启用提供商设为默认。
- 无租户默认提供商：自动回退到现有 `.env` 模型配置。
- 模型不可用：显式 SQL和内置确定性查询模板仍可执行；普通自然语言模型生成会明确失败，不会绕过安全校验猜测 SQL。

MySQL 部署需要应用 `V015__create_model_operations.sql`。项目的一键启动和 Docker API 启动流程会通过现有迁移脚本应用待执行迁移。
