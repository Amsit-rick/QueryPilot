from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_api_key: str = ""
    model_base_url: str = "https://api.deepseek.com"
    model_name: str = "deepseek-chat"
    model_provider: str = "direct"
    database_path: str = "./data/demo.db"
    max_rows: int = 1000
    query_stream_chunk_rows: int = Field(default=100, ge=1, le=1000)
    query_stream_heartbeat_seconds: float = Field(default=5.0, ge=1, le=60)
    query_stream_state_backend: Literal["memory", "redis"] = "memory"
    query_stream_state_ttl_seconds: int = Field(default=900, ge=30, le=86400)
    query_stream_state_max_events: int = Field(default=2048, ge=16, le=10000)
    query_stream_poll_seconds: float = Field(default=0.05, ge=0.01, le=5)
    conversation_compaction_threshold_turns: int = Field(default=8, ge=2, le=1000)
    conversation_recent_turns: int = Field(default=4, ge=1, le=100)
    conversation_retrieval_turns: int = Field(default=2, ge=0, le=20)
    conversation_context_max_chars: int = Field(default=12000, ge=2000, le=100000)
    analysis_branch_max_depth: int = Field(default=3, ge=1, le=20)
    analysis_branch_max_children: int = Field(default=20, ge=1, le=100)
    proactive_analysis_enabled: bool = False
    proactive_analysis_min_interval_minutes: int = Field(default=15, ge=1, le=1440)
    proactive_analysis_max_runs_per_day: int = Field(default=24, ge=1, le=100)
    proactive_analysis_max_daily_budget_usd: float = Field(default=2.0, ge=0, le=100)
    proactive_analysis_lease_seconds: int = Field(default=120, ge=5, le=3600)
    model_timeout_seconds: float = 30.0
    # httpx trusts the OS proxy configuration by default. On Windows that reads
    # the registry proxy (e.g. a local Clash at 127.0.0.1:7890), which can reset
    # long-lived model connections; set this to false to call the model directly.
    model_http_trust_env: bool = True
    llm_circuit_failures: int = 3
    llm_circuit_reset_seconds: int = 30
    llm_circuit_backend: str = "memory"
    mcp_runtime_backend: str = "memory"
    mcp_queue_timeout_seconds: float = Field(default=0.25, ge=0.01, le=30)
    mcp_max_arguments_bytes: int = Field(default=64 * 1024, ge=1024, le=10 * 1024 * 1024)
    mcp_max_result_bytes: int = Field(default=1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    mcp_max_sse_event_bytes: int = Field(default=1024 * 1024, ge=1024, le=10 * 1024 * 1024)
    mcp_max_discovered_tools: int = Field(default=200, ge=1, le=5000)
    mcp_max_tool_pages: int = Field(default=10, ge=1, le=100)
    sql_cache_ttl_seconds: int = 300
    sql_cache_max_entries: int = 512
    max_sql_length: int = 5000
    max_params: int = 50
    large_result_row_threshold: int = Field(default=800, ge=1, le=10000)
    query_cost_guard_enabled: bool = True
    query_cost_require_explain: bool = False
    query_cost_max_estimated_rows: int = Field(default=100000, ge=1, le=1000000000)
    query_cost_max_estimated_cost: float = Field(default=100000.0, ge=0)
    relationship_approval_required: bool = True
    relationship_min_confidence: float = Field(default=0.8, ge=0, le=1)
    schema_retrieval_enabled: bool = True
    schema_retrieval_table_threshold: int = Field(default=50, ge=0, le=100000)
    schema_retrieval_max_tables: int = Field(default=20, ge=1, le=500)
    schema_retrieval_max_columns_per_table: int = Field(default=40, ge=1, le=1000)
    schema_retrieval_max_hops: int = Field(default=3, ge=1, le=10)
    value_grounding_enabled: bool = True
    value_grounding_max_columns: int = Field(default=12, ge=1, le=100)
    value_grounding_max_values: int = Field(default=16, ge=1, le=100)
    value_grounding_max_value_chars: int = Field(default=64, ge=8, le=512)
    value_grounding_ttl_seconds: int = Field(default=300, ge=0, le=86400)
    schema_embedding_provider: Literal["disabled", "openai_compatible"] = "disabled"
    schema_embedding_base_url: str = ""
    schema_embedding_model: str = ""
    schema_embedding_api_key_env: str = "SCHEMA_EMBEDDING_API_KEY"
    schema_embedding_timeout_seconds: float = Field(default=10.0, ge=0.1, le=120)
    schema_embedding_batch_size: int = Field(default=64, ge=1, le=256)
    schema_embedding_cache_entries: int = Field(default=10000, ge=1, le=1000000)
    schema_embedding_max_documents: int = Field(default=5000, ge=1, le=100000)
    schema_embedding_max_document_chars: int = Field(default=4000, ge=64, le=50000)
    schema_embedding_max_total_chars: int = Field(default=2000000, ge=1024, le=100000000)
    schema_embedding_cache_backend: Literal["memory", "redis"] = "memory"
    schema_embedding_cache_ttl_seconds: int = Field(default=86400, ge=60, le=2592000)
    schema_embedding_circuit_backend: Literal["memory", "redis"] = "memory"
    schema_embedding_circuit_failures: int = Field(default=3, ge=1, le=20)
    schema_embedding_circuit_reset_seconds: int = Field(default=30, ge=1, le=3600)
    schema_embedding_remote_allowed: bool = False
    audit_log_path: str = "./data/audit.jsonl"
    audit_log_max_bytes: int = 10 * 1024 * 1024
    audit_log_backup_count: int = 5
    audit_stdout_enabled: bool = False
    catalog_path: str = "./data/catalog.json"
    policy_path: str = "./data/access_policy.json"
    datasources_path: str = "./data/datasources.json"
    default_datasource: str = "sqlite-demo"
    max_request_cost_usd: float = 0.08
    daily_budget_usd: float = 5.0
    budget_backend: str = "memory"
    cost_per_1k_tokens_usd: float = 0.00014
    export_dir: str = "./data/exports"
    export_job_ttl_seconds: int = 3600
    export_execution_mode: str = "inline"
    export_max_retries: int = 2
    redis_url: str = "redis://127.0.0.1:6379/0"
    agent_runtime: str = "bounded"
    agent_max_repairs: int = 2
    agent_max_schema_expansions: int = Field(default=1, ge=0, le=10)
    agent_max_execution_retries: int = Field(default=1, ge=0, le=10)
    agent_max_reflection_steps: int = Field(default=12, ge=1, le=100)
    agent_reflection_mode: Literal["off", "shadow", "enforced"] = "shadow"
    agent_reflection_llm_enabled: bool = False
    agent_reflection_critic_timeout_seconds: float = Field(default=8.0, ge=0.1, le=60)
    agent_reflection_critic_min_confidence: float = Field(default=0.8, ge=0, le=1)
    agent_reflection_eval_report_path: str = "./artifacts/reflection-eval.json"
    agent_reflection_release_gate_path: str = "./artifacts/reflection-release-gate.json"
    agent_trace_enabled: bool = True
    agent_trace_path: str = "./data/agent-trace.jsonl"
    agent_trace_exporter: Literal["jsonl", "otlp"] = "jsonl"
    agent_trace_otlp_endpoint: str = ""
    agent_trace_otlp_headers: str = ""
    agent_trace_service_name: str = "query-pilot"
    agent_checkpoint_enabled: bool = False
    agent_checkpoint_ttl_seconds: int = 3600
    agent_recovery_lease_seconds: int = Field(default=60, ge=5, le=3600)
    agent_approval_ttl_seconds: int = 3600
    agent_low_confidence_threshold: float = Field(default=0.65, ge=0, le=1)
    agent_high_risk_row_threshold: int = Field(default=5000, ge=1, le=10000)
    auth_enabled: bool = False
    api_keys: str = ""
    database_backend: str = "sqlite"
    postgres_dsn: str = ""
    postgres_readonly_dsn: str = ""
    gateway_connect_timeout_seconds: float = Field(default=5.0, ge=1, le=300)
    gateway_query_timeout_seconds: float = Field(default=30.0, ge=1, le=300)
    oracle_dsn: str = ""
    oracle_user: str = ""
    oracle_password: str = ""
    oracle_plan_user: str = ""
    oracle_plan_password: str = ""
    sqlserver_host: str = ""
    sqlserver_port: int = 1433
    sqlserver_database: str = ""
    sqlserver_user: str = ""
    sqlserver_password: str = ""
    clickhouse_host: str = ""
    clickhouse_port: int = 9000
    clickhouse_database: str = ""
    clickhouse_user: str = ""
    clickhouse_password: str = ""
    clickhouse_secure: bool = False
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3307
    mysql_database: str = "query_agent"
    mysql_user: str = "query_app"
    mysql_password: str = ""
    mysql_query_user: str = ""
    mysql_query_password: str = ""
    mysql_max_connections: int = 10
    mysql_pool_idle_ttl_seconds: int = Field(default=300, ge=0, le=86400)
    mysql_connect_timeout_seconds: float = 5.0
    rate_limit_per_minute: int = 60
    rate_limit_backend: str = "memory"
    metrics_token: str = ""
    jwt_secret: str = ""
    jwt_previous_secrets: str = ""
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = ""
    jwt_audience: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
