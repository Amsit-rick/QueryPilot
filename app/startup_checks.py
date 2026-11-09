from math import isfinite
import os
from pathlib import Path
import re
from urllib.parse import urlparse

from .config import settings


_ENV_REFERENCE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        from ipaddress import ip_address

        return ip_address(host).is_loopback
    except ValueError:
        return False


def validate_production_config() -> list[str]:
    errors = []
    database_backend = str(settings.database_backend).lower()
    if database_backend not in {"sqlite", "mysql", "postgres", "postgresql"}:
        errors.append("DATABASE_BACKEND 必须为 sqlite、mysql 或 postgresql")
    if database_backend in {"postgres", "postgresql"} and not settings.postgres_dsn.strip():
        errors.append("PostgreSQL 数据库必须配置 POSTGRES_DSN")
    if not 1 <= settings.mysql_port <= 65535:
        errors.append("MYSQL_PORT 必须在 1 到 65535 之间")
    if not isfinite(settings.mysql_connect_timeout_seconds) or settings.mysql_connect_timeout_seconds <= 0:
        errors.append("MYSQL_CONNECT_TIMEOUT_SECONDS 必须为有限正数")
    if settings.max_rows < 1:
        errors.append("MAX_ROWS 必须大于 0")
    if settings.audit_log_max_bytes < 1024:
        errors.append("AUDIT_LOG_MAX_BYTES 必须至少为 1024")
    if settings.audit_log_backup_count < 1 or settings.audit_log_backup_count > 100:
        errors.append("AUDIT_LOG_BACKUP_COUNT 必须在 1 到 100 之间")
    if not isfinite(settings.model_timeout_seconds) or settings.model_timeout_seconds <= 0:
        errors.append("MODEL_TIMEOUT_SECONDS 必须为有限正数")
    if settings.rate_limit_per_minute < 1:
        errors.append("RATE_LIMIT_PER_MINUTE 必须大于 0")
    if not isfinite(settings.max_request_cost_usd) or settings.max_request_cost_usd < 0:
        errors.append("MAX_REQUEST_COST_USD 必须为有限非负数")
    if not isfinite(settings.daily_budget_usd) or settings.daily_budget_usd < 0:
        errors.append("DAILY_BUDGET_USD 必须为有限非负数")
    if not isfinite(settings.cost_per_1k_tokens_usd) or settings.cost_per_1k_tokens_usd < 0:
        errors.append("COST_PER_1K_TOKENS_USD 必须为有限非负数")
    if settings.model_provider not in {"direct", "langchain"}:
        errors.append("MODEL_PROVIDER 必须为 direct 或 langchain")
    embedding_provider = str(settings.schema_embedding_provider).lower()
    if embedding_provider not in {"disabled", "openai_compatible"}:
        errors.append("SCHEMA_EMBEDDING_PROVIDER 必须为 disabled 或 openai_compatible")
    elif embedding_provider == "openai_compatible":
        parsed_embedding_url = urlparse(settings.schema_embedding_base_url.strip())
        if (
            parsed_embedding_url.scheme not in {"http", "https"}
            or not parsed_embedding_url.netloc
            or parsed_embedding_url.username
            or parsed_embedding_url.password
            or parsed_embedding_url.query
            or parsed_embedding_url.fragment
        ):
            errors.append("SCHEMA_EMBEDDING_BASE_URL 必须是无凭据、查询参数和片段的 HTTP(S) 地址")
        elif not _is_loopback_host(parsed_embedding_url.hostname):
            if parsed_embedding_url.scheme != "https":
                errors.append("远程 Schema Embedding 服务必须使用 HTTPS")
            if not settings.schema_embedding_remote_allowed:
                errors.append("向远程服务发送授权 Schema 元数据前必须显式启用 SCHEMA_EMBEDDING_REMOTE_ALLOWED")
        if not settings.schema_embedding_model.strip():
            errors.append("SCHEMA_EMBEDDING_MODEL 不能为空")
        if not _ENV_REFERENCE.fullmatch(settings.schema_embedding_api_key_env.strip()):
            errors.append("SCHEMA_EMBEDDING_API_KEY_ENV 必须是大写环境变量引用")
        if settings.schema_embedding_cache_backend not in {"memory", "redis"}:
            errors.append("SCHEMA_EMBEDDING_CACHE_BACKEND 必须为 memory 或 redis")
        if settings.schema_embedding_circuit_backend not in {"memory", "redis"}:
            errors.append("SCHEMA_EMBEDDING_CIRCUIT_BACKEND 必须为 memory 或 redis")
        if (
            settings.schema_embedding_cache_backend == "redis"
            or settings.schema_embedding_circuit_backend == "redis"
        ) and not settings.redis_url.strip():
            errors.append("Schema Embedding 的 Redis 缓存或熔断必须配置 REDIS_URL")
    multiprocess_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR", "").strip()
    if multiprocess_dir:
        path = Path(multiprocess_dir)
        if not path.is_dir():
            errors.append("PROMETHEUS_MULTIPROC_DIR 必须指向已存在目录")
        elif not os.access(path, os.W_OK):
            errors.append("PROMETHEUS_MULTIPROC_DIR 必须可写")
    if settings.agent_runtime not in {"bounded", "langgraph"}:
        errors.append("AGENT_RUNTIME 必须为 bounded 或 langgraph")
    if settings.agent_max_repairs < 0 or settings.agent_max_repairs > 5:
        errors.append("AGENT_MAX_REPAIRS 必须在 0 到 5 之间")
    if settings.llm_circuit_backend not in {"memory", "redis"}:
        errors.append("LLM_CIRCUIT_BACKEND 必须为 memory 或 redis")
    if settings.llm_circuit_backend == "redis" and not settings.redis_url.strip():
        errors.append("Redis 模型熔断必须配置 REDIS_URL")
    if settings.mcp_runtime_backend not in {"memory", "redis"}:
        errors.append("MCP_RUNTIME_BACKEND 必须为 memory 或 redis")
    if settings.mcp_runtime_backend == "redis" and not settings.redis_url.strip():
        errors.append("Redis MCP 调用治理必须配置 REDIS_URL")
    if settings.query_stream_state_backend not in {"memory", "redis"}:
        errors.append("QUERY_STREAM_STATE_BACKEND 必须为 memory 或 redis")
    if settings.query_stream_state_backend == "redis" and not settings.redis_url.strip():
        errors.append("Redis 查询流状态必须配置 REDIS_URL")
    if settings.agent_checkpoint_enabled and not settings.redis_url.strip():
        errors.append("启用 Agent checkpoint 时必须配置 REDIS_URL")
    if settings.export_execution_mode not in {"inline", "redis"}:
        errors.append("EXPORT_EXECUTION_MODE 必须为 inline 或 redis")
    if settings.export_execution_mode == "redis" and not settings.redis_url.strip():
        errors.append("Redis 模式必须配置 REDIS_URL")
    if settings.rate_limit_backend not in {"memory", "redis"}:
        errors.append("RATE_LIMIT_BACKEND 必须为 memory 或 redis")
    if settings.rate_limit_backend == "redis" and not settings.redis_url.strip():
        errors.append("Redis 限流必须配置 REDIS_URL")
    if settings.budget_backend not in {"memory", "redis"}:
        errors.append("BUDGET_BACKEND 必须为 memory 或 redis")
    if settings.budget_backend == "redis" and not settings.redis_url.strip():
        errors.append("Redis 预算必须配置 REDIS_URL")
    if settings.auth_enabled:
        if not settings.jwt_secret or len(settings.jwt_secret) < 32:
            errors.append("JWT_SECRET 必须至少 32 字符，生产应从 Vault/KMS 注入")
        origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
        if not origins or "*" in origins:
            errors.append("生产 CORS_ORIGINS 不能为通配符或空值")
        else:
            for origin in origins:
                parsed = urlparse(origin)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                    errors.append("生产 CORS_ORIGINS 必须是 scheme://host[:port] 来源列表")
                    break
        if settings.mysql_password in {"", "query_app_dev", "root_dev_only"}:
            errors.append("生产不得使用开发数据库密码")
        if database_backend == "mysql":
            if not settings.mysql_query_user.strip() or not settings.mysql_query_password:
                errors.append("生产 MySQL 业务查询必须配置独立的 MYSQL_QUERY_USER/MYSQL_QUERY_PASSWORD 只读凭据")
            elif settings.mysql_query_user == settings.mysql_user:
                errors.append("生产 MySQL 控制面账号与业务查询账号必须分离")
        if database_backend in {"postgres", "postgresql"} and not settings.postgres_readonly_dsn.strip():
            errors.append("生产 PostgreSQL 业务查询必须配置 POSTGRES_READONLY_DSN")
        if len(settings.metrics_token.strip()) < 16:
            errors.append("生产 METRICS_TOKEN 必须至少 16 字符")
    return errors
