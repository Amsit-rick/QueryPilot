import pytest

from app.startup_checks import validate_production_config
from app.main import lifespan
import asyncio


def test_development_config_does_not_fail_checks():
    assert validate_production_config() == []


def test_production_config_rejects_short_jwt(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", "short")
    assert validate_production_config()


def test_production_requires_metrics_token(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "metrics_token", "short")
    assert any("METRICS_TOKEN" in item for item in validate_production_config())


def test_production_mysql_requires_separate_readonly_query_credentials(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "database_backend", "mysql")
    monkeypatch.setattr(settings, "mysql_query_user", "")
    monkeypatch.setattr(settings, "mysql_query_password", "")
    assert any("MYSQL_QUERY_USER" in item for item in validate_production_config())

    monkeypatch.setattr(settings, "mysql_query_user", settings.mysql_user)
    monkeypatch.setattr(settings, "mysql_query_password", "separate-password")
    assert any("账号必须分离" in item for item in validate_production_config())


def test_production_postgres_requires_readonly_query_dsn(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "database_backend", "postgres")
    monkeypatch.setattr(settings, "postgres_dsn", "postgresql://control-plane")
    monkeypatch.setattr(settings, "postgres_readonly_dsn", "")
    assert any("POSTGRES_READONLY_DSN" in item for item in validate_production_config())


@pytest.mark.parametrize("origins", ["*", "", "localhost:5173", "http://example.com/path", "http://example.com?x=1"])
def test_production_rejects_invalid_cors_origins(monkeypatch, origins):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "cors_origins", origins)
    assert any("CORS_ORIGINS" in item for item in validate_production_config())

def test_redis_mode_requires_url(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "export_execution_mode", "redis")
    monkeypatch.setattr(settings, "redis_url", "")
    assert any("REDIS_URL" in item for item in validate_production_config())

def test_query_stream_redis_backend_requires_url(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "query_stream_state_backend", "redis")
    monkeypatch.setattr(settings, "redis_url", "")

    assert any("查询流" in item and "REDIS_URL" in item for item in validate_production_config())

def test_invalid_export_mode_is_rejected(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "export_execution_mode", "sidecar")
    assert any("EXPORT_EXECUTION_MODE" in item for item in validate_production_config())

def test_invalid_agent_runtime_is_rejected(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "agent_runtime", "magic")
    assert any("AGENT_RUNTIME" in item for item in validate_production_config())

def test_agent_repair_limit_is_bounded(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "agent_max_repairs", 99)
    assert any("AGENT_MAX_REPAIRS" in item for item in validate_production_config())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("max_rows", 0, "MAX_ROWS"),
        ("model_timeout_seconds", float("nan"), "MODEL_TIMEOUT_SECONDS"),
        ("rate_limit_per_minute", 0, "RATE_LIMIT_PER_MINUTE"),
        ("max_request_cost_usd", -1, "MAX_REQUEST_COST_USD"),
        ("daily_budget_usd", float("inf"), "DAILY_BUDGET_USD"),
        ("cost_per_1k_tokens_usd", -0.1, "COST_PER_1K_TOKENS_USD"),
        ("audit_log_max_bytes", 512, "AUDIT_LOG_MAX_BYTES"),
        ("audit_log_backup_count", 0, "AUDIT_LOG_BACKUP_COUNT"),
    ],
)
def test_invalid_runtime_limits_are_rejected(monkeypatch, field, value, message):
    from app.config import settings

    monkeypatch.setattr(settings, field, value)
    assert any(message in item for item in validate_production_config())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("database_backend", "oracle", "DATABASE_BACKEND"),
        ("mysql_port", 70000, "MYSQL_PORT"),
        ("mysql_connect_timeout_seconds", 0, "MYSQL_CONNECT_TIMEOUT_SECONDS"),
    ],
)
def test_invalid_database_configuration_is_rejected(monkeypatch, field, value, message):
    from app.config import settings

    monkeypatch.setattr(settings, field, value)
    assert any(message in item for item in validate_production_config())


def test_postgres_alias_requires_dsn(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "database_backend", "postgres")
    monkeypatch.setattr(settings, "postgres_dsn", "")
    assert any("POSTGRES_DSN" in item for item in validate_production_config())

def test_invalid_model_provider_is_rejected(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "model_provider", "unknown")
    assert any("MODEL_PROVIDER" in item for item in validate_production_config())


def test_remote_schema_embedding_requires_https_and_explicit_metadata_consent(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "schema_embedding_provider", "openai_compatible")
    monkeypatch.setattr(settings, "schema_embedding_base_url", "http://embedding.example.com/v1")
    monkeypatch.setattr(settings, "schema_embedding_model", "embedding-model")
    monkeypatch.setattr(settings, "schema_embedding_api_key_env", "SCHEMA_EMBEDDING_API_KEY")
    monkeypatch.setattr(settings, "schema_embedding_remote_allowed", False)

    errors = validate_production_config()
    assert any("HTTPS" in item for item in errors)
    assert any("SCHEMA_EMBEDDING_REMOTE_ALLOWED" in item for item in errors)


def test_loopback_schema_embedding_does_not_require_remote_metadata_consent(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "schema_embedding_provider", "openai_compatible")
    monkeypatch.setattr(settings, "schema_embedding_base_url", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(settings, "schema_embedding_model", "local-embedding")
    monkeypatch.setattr(settings, "schema_embedding_api_key_env", "SCHEMA_EMBEDDING_API_KEY")
    monkeypatch.setattr(settings, "schema_embedding_remote_allowed", False)

    assert not any("SCHEMA_EMBEDDING" in item for item in validate_production_config())


def test_schema_embedding_configuration_requires_model_and_secret_reference(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "schema_embedding_provider", "openai_compatible")
    monkeypatch.setattr(settings, "schema_embedding_base_url", "https://embedding.example.com/v1")
    monkeypatch.setattr(settings, "schema_embedding_model", "")
    monkeypatch.setattr(settings, "schema_embedding_api_key_env", "inline-secret")
    monkeypatch.setattr(settings, "schema_embedding_remote_allowed", True)

    errors = validate_production_config()
    assert any("SCHEMA_EMBEDDING_MODEL" in item for item in errors)
    assert any("SCHEMA_EMBEDDING_API_KEY_ENV" in item for item in errors)


def test_schema_embedding_redis_modes_require_redis_url(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "schema_embedding_provider", "openai_compatible")
    monkeypatch.setattr(settings, "schema_embedding_base_url", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(settings, "schema_embedding_model", "local-embedding")
    monkeypatch.setattr(settings, "schema_embedding_api_key_env", "SCHEMA_EMBEDDING_API_KEY")
    monkeypatch.setattr(settings, "schema_embedding_cache_backend", "redis")
    monkeypatch.setattr(settings, "schema_embedding_circuit_backend", "redis")
    monkeypatch.setattr(settings, "redis_url", "")

    assert any("Schema Embedding" in item and "REDIS_URL" in item for item in validate_production_config())

def test_invalid_rate_limit_backend_is_rejected(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "rate_limit_backend", "cluster")
    assert any("RATE_LIMIT_BACKEND" in item for item in validate_production_config())

def test_redis_rate_limit_backend_requires_url(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "rate_limit_backend", "redis")
    monkeypatch.setattr(settings, "redis_url", "")
    assert any("Redis 限流" in item for item in validate_production_config())

def test_invalid_llm_circuit_backend_is_rejected(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "llm_circuit_backend", "cluster")
    assert any("LLM_CIRCUIT_BACKEND" in item for item in validate_production_config())

def test_redis_llm_circuit_backend_requires_url(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "llm_circuit_backend", "redis")
    monkeypatch.setattr(settings, "redis_url", "")
    assert any("模型熔断" in item for item in validate_production_config())

def test_invalid_budget_backend_is_rejected(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "budget_backend", "cluster")
    assert any("BUDGET_BACKEND" in item for item in validate_production_config())

def test_redis_budget_backend_requires_url(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "budget_backend", "redis")
    monkeypatch.setattr(settings, "redis_url", "")
    assert any("Redis 预算" in item for item in validate_production_config())

def test_invalid_mcp_runtime_backend_is_rejected(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_runtime_backend", "cluster")
    assert any("MCP_RUNTIME_BACKEND" in item for item in validate_production_config())

def test_redis_mcp_runtime_backend_requires_url(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_runtime_backend", "redis")
    monkeypatch.setattr(settings, "redis_url", "")
    assert any("MCP 调用治理" in item for item in validate_production_config())

def test_lifespan_rejects_invalid_production_configuration(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "rate_limit_backend", "cluster")
    async def run():
        async with lifespan(None):
            pass
    with pytest.raises(RuntimeError, match="生产配置校验失败.*RATE_LIMIT_BACKEND"):
        asyncio.run(run())
