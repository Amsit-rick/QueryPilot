"""Data source registry: multiple read-only backends with role authorization.

The registry is a versioned JSON file (``settings.datasources_path``); when
missing, a built-in default with a SQLite demo and a MySQL backend is used.
Connection secrets for MySQL live in ``settings`` (env), never in the registry.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import settings

SUPPORTED_BACKENDS = frozenset({"sqlite", "mysql", "postgres", "postgresql", "oracle", "sqlserver", "clickhouse"})


def _remote_gateway(ds):
    from .gateways.factory import REMOTE_GATEWAYS
    backend = str(ds.get("backend", "")).lower()
    gateway_type = REMOTE_GATEWAYS.get(backend)
    if gateway_type:
        options = {"allowed_tables": ds.get("allowed_tables")}
        if backend == "oracle" and "schema_owner" in ds:
            options["schema_owner"] = ds.get("schema_owner")
        if backend == "oracle" and "cost_estimate_enabled" in ds:
            options["cost_estimate_enabled"] = ds.get("cost_estimate_enabled")
        return gateway_type(gateway_id=ds["id"], name=ds.get("name", ds["id"]), **options)
    return None


def _default_registry() -> dict:
    return {
        "version": "2026-09-03-v1",
        "datasources": [
            {"id": "sqlite-demo", "name": "SQLite 演示库", "backend": "sqlite", "path": settings.database_path, "readonly": True, "roles": ["admin", "analyst", "viewer"]},
            {"id": "mysql-main", "name": "MySQL 生产库", "backend": "mysql", "readonly": True, "roles": ["admin", "analyst"]},
        ],
    }


def load_registry() -> dict:
    path = Path(settings.datasources_path)
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return _default_registry()


def _setting_is_configured(name: str) -> bool:
    value = getattr(settings, name, "")
    return isinstance(value, str) and bool(value.strip())


def _datasource_is_enabled(ds: dict) -> bool:
    """Fail closed for catalog entries that depend on external credentials."""
    if not ds.get("enabled_if_configured", False):
        return True

    backend = str(ds.get("backend", "")).lower()
    if backend in {"postgres", "postgresql"}:
        return _setting_is_configured("postgres_readonly_dsn")
    if backend == "sqlserver":
        return all(
            _setting_is_configured(name)
            for name in (
                "sqlserver_host",
                "sqlserver_database",
                "sqlserver_user",
                "sqlserver_password",
            )
        )
    if backend == "clickhouse":
        return all(
            _setting_is_configured(name)
            for name in (
                "clickhouse_host",
                "clickhouse_database",
                "clickhouse_user",
                "clickhouse_password",
            )
        )
    if backend != "oracle":
        return False

    required_settings = ["oracle_dsn", "oracle_user", "oracle_password"]
    if ds.get("cost_estimate_enabled", False):
        required_settings.extend(["oracle_plan_user", "oracle_plan_password"])
    return all(_setting_is_configured(name) for name in required_settings)


def list_datasources() -> list[dict]:
    datasources = load_registry().get("datasources", [])
    return [ds for ds in datasources if isinstance(ds, dict) and _datasource_is_enabled(ds)]


def _public_view(ds: dict) -> dict:
    return {
        "id": ds["id"],
        "name": ds.get("name", ds["id"]),
        "backend": ds.get("backend"),
        "readonly": ds.get("readonly", True),
    }


def list_data_sources_for(identity: dict) -> list[dict]:
    """Datasources the identity's roles are authorized to use."""
    roles = set(identity.get("roles") or [])
    result = []
    for ds in list_datasources():
        if ds.get("backend", "sqlite").lower() not in SUPPORTED_BACKENDS:
            continue
        if "admin" in roles or (roles & set(ds.get("roles") or [])):
            result.append(_public_view(ds))
    return result


def get_datasource(ds_id: str) -> dict | None:
    for ds in list_datasources():
        if ds.get("id") == ds_id:
            return ds
    return None


def role_can_use(identity: dict, ds: dict) -> bool:
    roles = set(identity.get("roles") or [])
    if "admin" in roles:
        return True
    return bool(roles & set(ds.get("roles") or []))


def datasource_schema(ds: dict) -> dict[str, set[str]]:
    backend = str(ds.get("backend", "sqlite")).lower()
    gateway = _remote_gateway(ds)
    if gateway:
        return {table: set(columns) for table, columns in gateway.schema().items()}
    if backend == "mysql":
        from .gateways.mysql_gateway import MySQLGateway
        return {table: set(columns) for table, columns in MySQLGateway(
            gateway_id=ds["id"], name=ds.get("name", ds["id"]), allowed_tables=ds.get("allowed_tables")
        ).schema().items()}
    if backend in {"postgres", "postgresql"}:
        from .gateways.postgres_gateway import PostgreSQLGateway
        return {table: set(columns) for table, columns in PostgreSQLGateway(
            gateway_id=ds["id"], name=ds.get("name", ds["id"]), allowed_tables=ds.get("allowed_tables")
        ).schema().items()}
    if backend == "sqlite":
        from .db import schema
        return schema(ds.get("path", settings.database_path))
    raise ValueError(f"E_DATASOURCE_BACKEND:暂不支持数据源类型 {backend}")


def datasource_schema_details(ds: dict) -> dict:
    backend = str(ds.get("backend", "sqlite")).lower()
    gateway = _remote_gateway(ds)
    if gateway and hasattr(gateway, "schema_details"):
        return gateway.schema_details().__dict__
    if backend == "sqlite":
        from .db import schema_details
        return schema_details(ds.get("path", settings.database_path))
    if backend == "mysql":
        from .mysql_db import mysql_schema_details
        return mysql_schema_details()
    if backend in {"postgres", "postgresql"}:
        from .postgres_db import postgres_schema_details
        return postgres_schema_details()
    return {"tables": {}, "relationships": []}


def execute_datasource(ds: dict, sql: str, params: dict, max_rows: int, offset: int = 0):
    backend = str(ds.get("backend", "sqlite")).lower()
    gateway = _remote_gateway(ds)
    if gateway:
        from .domain.data_gateway import GatewayQuery
        result = gateway.query(GatewayQuery(sql, params, max_rows, offset))
        return result.columns, result.rows, result.truncated
    # Workers and future non-HTTP callers must pass the same structural SQL
    # boundary as the main Agent path before a database connection is opened.
    from .guardrails import bound_query_rows, validate_sql
    sql = validate_sql(
        sql,
        datasource_schema(ds),
        settings.max_sql_length,
        dialect=backend,
        max_params=settings.max_params,
    )
    sql = bound_query_rows(sql, dialect=backend, max_rows=max_rows, offset=offset)
    if backend == "mysql":
        from .mysql_db import execute_mysql
        return execute_mysql(sql, params, max_rows, offset)
    if backend in {"postgres", "postgresql"}:
        from .postgres_db import execute_postgres
        return execute_postgres(sql, params, max_rows, offset)
    if backend == "sqlite":
        from .db import execute_readonly
        return execute_readonly(ds.get("path", settings.database_path), sql, max_rows, params, settings.model_timeout_seconds, offset)
    raise ValueError(f"E_DATASOURCE_BACKEND:暂不支持数据源类型 {backend}")


def test_connection(ds: dict) -> dict:
    backend = str(ds.get("backend", "sqlite")).lower()
    gateway = _remote_gateway(ds)
    if gateway:
        health = gateway.test_connection()
        return {"ok": health.status == "healthy", "backend": backend, "message": health.message}
    if backend == "mysql":
        from .mysql_db import query_connection
        try:
            with query_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            return {"ok": True, "backend": "mysql", "message": "连接成功"}
        except Exception as exc:
            return {"ok": False, "backend": "mysql", "message": "MySQL 连接失败，请检查服务状态和凭据"}
    if backend in {"postgres", "postgresql"}:
        from .postgres_db import test_postgres
        result = test_postgres()
        if not result.get("ok"):
            return {"ok": False, "backend": backend, "message": "PostgreSQL 连接失败，请检查服务状态和凭据"}
        return result
    if backend == "sqlite":
        path = Path(ds.get("path", settings.database_path))
        return {"ok": path.is_file(), "backend": "sqlite", "message": "文件存在" if path.is_file() else "SQLite 数据库文件不存在"}
    return {"ok": False, "backend": backend, "message": f"暂不支持数据源类型：{backend}"}
