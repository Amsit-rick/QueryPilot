import json

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.auth import require_identity
from app.config import settings
from app.datasources import (
    datasource_schema,
    execute_datasource,
    get_datasource,
    list_data_sources_for,
    list_datasources,
    test_connection as check_connection,
)


@pytest.fixture(autouse=True)
def isolate_default_datasource_registry(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "datasources_path", str(tmp_path / "missing-datasources.json"))


@pytest.fixture()
def client():
    app = main_module.app
    app.dependency_overrides.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()


def _override(identity):
    main_module.app.dependency_overrides[require_identity] = lambda: identity


def _write_registry(path, *datasources):
    path.write_text(
        json.dumps({"version": "test", "datasources": list(datasources)}),
        encoding="utf-8",
    )


def _oracle_datasource():
    return {
        "id": "oracle-main",
        "name": "Oracle 19c 业务库",
        "backend": "oracle",
        "schema_owner": "QUERYPILOT_OWNER",
        "cost_estimate_enabled": True,
        "readonly": True,
        "enabled_if_configured": True,
        "roles": ["admin", "analyst"],
        "allowed_tables": ["CUSTOMERS", "SALES"],
    }


def _configure_oracle(monkeypatch):
    monkeypatch.setattr(settings, "oracle_dsn", "db.example:1521/ORCLPDB1")
    monkeypatch.setattr(settings, "oracle_user", "query_user")
    monkeypatch.setattr(settings, "oracle_password", "query_password")
    monkeypatch.setattr(settings, "oracle_plan_user", "plan_user")
    monkeypatch.setattr(settings, "oracle_plan_password", "plan_password")


def _configured_remote_datasource(backend):
    return {
        "id": f"{backend}-main",
        "name": f"{backend} 业务库",
        "backend": backend,
        "readonly": True,
        "enabled_if_configured": True,
        "roles": ["admin", "analyst"],
        "allowed_tables": ["sales", "customers"],
    }


def _configure_remote_backend(monkeypatch, backend):
    if backend == "postgres":
        monkeypatch.setattr(
            settings,
            "postgres_readonly_dsn",
            "postgresql://reader:password@db.example:5432/query_agent",
        )
        return ["postgres_readonly_dsn"]
    if backend == "sqlserver":
        monkeypatch.setattr(settings, "sqlserver_host", "db.example")
        monkeypatch.setattr(settings, "sqlserver_database", "query_agent")
        monkeypatch.setattr(settings, "sqlserver_user", "reader")
        monkeypatch.setattr(settings, "sqlserver_password", "password")
        return [
            "sqlserver_host",
            "sqlserver_database",
            "sqlserver_user",
            "sqlserver_password",
        ]
    if backend == "clickhouse":
        monkeypatch.setattr(settings, "clickhouse_host", "db.example")
        monkeypatch.setattr(settings, "clickhouse_database", "query_agent")
        monkeypatch.setattr(settings, "clickhouse_user", "reader")
        monkeypatch.setattr(settings, "clickhouse_password", "password")
        return [
            "clickhouse_host",
            "clickhouse_database",
            "clickhouse_user",
            "clickhouse_password",
        ]
    raise AssertionError(f"unsupported test backend: {backend}")


def test_viewer_only_sees_sqlite():
    ids = [d["id"] for d in list_data_sources_for({"roles": ["viewer"]})]
    assert ids == ["sqlite-demo"]


def test_analyst_and_admin_see_both():
    assert [d["id"] for d in list_data_sources_for({"roles": ["analyst"]})] == ["sqlite-demo", "mysql-main"]
    assert [d["id"] for d in list_data_sources_for({"roles": ["admin"]})] == ["sqlite-demo", "mysql-main"]


def test_configured_oracle_is_visible_to_admin_and_analyst(monkeypatch, tmp_path):
    registry = tmp_path / "datasources.json"
    _write_registry(registry, _oracle_datasource())
    monkeypatch.setattr(settings, "datasources_path", str(registry))
    _configure_oracle(monkeypatch)

    assert [item["id"] for item in list_datasources()] == ["oracle-main"]
    assert [item["id"] for item in list_data_sources_for({"roles": ["admin"]})] == ["oracle-main"]
    assert [item["id"] for item in list_data_sources_for({"roles": ["analyst"]})] == ["oracle-main"]


@pytest.mark.parametrize(
    "missing_setting",
    ["oracle_dsn", "oracle_user", "oracle_password", "oracle_plan_user", "oracle_plan_password"],
)
def test_oracle_is_hidden_when_required_configuration_is_incomplete(
    monkeypatch, tmp_path, missing_setting
):
    registry = tmp_path / "datasources.json"
    _write_registry(registry, _oracle_datasource())
    monkeypatch.setattr(settings, "datasources_path", str(registry))
    _configure_oracle(monkeypatch)
    monkeypatch.setattr(settings, missing_setting, "")

    assert list_datasources() == []
    assert get_datasource("oracle-main") is None


def test_configured_oracle_remains_hidden_from_viewer(monkeypatch, tmp_path):
    registry = tmp_path / "datasources.json"
    _write_registry(registry, _oracle_datasource())
    monkeypatch.setattr(settings, "datasources_path", str(registry))
    _configure_oracle(monkeypatch)

    assert list_data_sources_for({"roles": ["viewer"]}) == []


def test_oracle_registry_preserves_owner_cost_and_table_options(monkeypatch, tmp_path):
    registry = tmp_path / "datasources.json"
    oracle = _oracle_datasource()
    _write_registry(registry, oracle)
    monkeypatch.setattr(settings, "datasources_path", str(registry))
    _configure_oracle(monkeypatch)

    assert get_datasource("oracle-main") == oracle


@pytest.mark.parametrize("backend", ["postgres", "sqlserver", "clickhouse"])
def test_configured_remote_datasource_is_visible(monkeypatch, tmp_path, backend):
    registry = tmp_path / "datasources.json"
    datasource = _configured_remote_datasource(backend)
    _write_registry(registry, datasource)
    monkeypatch.setattr(settings, "datasources_path", str(registry))
    _configure_remote_backend(monkeypatch, backend)

    assert list_datasources() == [datasource]
    assert [item["id"] for item in list_data_sources_for({"roles": ["admin"]})] == [
        datasource["id"]
    ]
    assert [item["id"] for item in list_data_sources_for({"roles": ["analyst"]})] == [
        datasource["id"]
    ]
    assert list_data_sources_for({"roles": ["viewer"]}) == []


@pytest.mark.parametrize("backend", ["postgres", "sqlserver", "clickhouse"])
def test_remote_datasource_is_hidden_when_configuration_is_incomplete(
    monkeypatch, tmp_path, backend
):
    registry = tmp_path / "datasources.json"
    datasource = _configured_remote_datasource(backend)
    _write_registry(registry, datasource)
    monkeypatch.setattr(settings, "datasources_path", str(registry))
    required_settings = _configure_remote_backend(monkeypatch, backend)

    for missing_setting in required_settings:
        monkeypatch.setattr(settings, missing_setting, "")
        assert list_datasources() == []
        assert get_datasource(datasource["id"]) is None
        _configure_remote_backend(monkeypatch, backend)


def test_datasource_schema_endpoint(client):
    _override({"subject": "local-dev", "tenant_id": "default", "roles": ["analyst"]})
    body = client.get("/api/v1/data-sources/sqlite-demo/schema").json()
    assert set(body["sales"]) == {"id", "region", "amount", "sold_at"}
    assert set(body["customers"]) == {"id", "name", "region"}


def test_datasource_schema_forbidden_for_viewer(client):
    _override({"subject": "local-dev", "tenant_id": "default", "roles": ["viewer"]})
    assert client.get("/api/v1/data-sources/mysql-main/schema").status_code == 403


def test_connection_test_requires_manage(client):
    _override({"subject": "1", "tenant_id": "default", "roles": ["analyst"]})
    assert client.post("/api/v1/data-sources/sqlite-demo/test").status_code == 403


def test_sqlite_connection_test(client):
    _override({"subject": "1", "tenant_id": "default", "roles": ["admin"]})
    body = client.post("/api/v1/data-sources/sqlite-demo/test").json()
    assert body["ok"] is True and body["backend"] == "sqlite"


def test_refresh_requires_manage(client):
    _override({"subject": "1", "tenant_id": "default", "roles": ["analyst"]})
    assert client.post("/api/v1/data-sources/sqlite-demo/refresh").status_code == 403


def test_refresh_returns_schema(client):
    _override({"subject": "1", "tenant_id": "default", "roles": ["admin"]})
    body = client.post("/api/v1/data-sources/sqlite-demo/refresh").json()
    assert "sales" in body and "customers" in body


def test_query_sqlite_datasource(client):
    _override({"subject": "local-dev", "tenant_id": "default", "roles": ["analyst"]})
    body = client.post("/api/v1/query", json={"question": "查询", "sql": "SELECT region FROM sales", "datasource": "sqlite-demo"}).json()
    assert body["row_count"] >= 3


def test_query_forbidden_datasource(client):
    _override({"subject": "local-dev", "tenant_id": "default", "roles": ["viewer"]})
    resp = client.post("/api/v1/query", json={"question": "查询", "sql": "SELECT region FROM sales", "datasource": "mysql-main"})
    assert resp.status_code == 403


def test_unsupported_backend_never_falls_back_to_sqlite():
    oracle = {"id": "unsupported", "backend": "unsupported", "path": "data/demo.db"}
    assert check_connection(oracle)["ok"] is False
    with pytest.raises(ValueError, match="E_DATASOURCE_BACKEND"):
        datasource_schema(oracle)
    with pytest.raises(ValueError, match="E_DATASOURCE_BACKEND"):
        execute_datasource(oracle, "SELECT 1", {}, 10)


def test_legacy_connection_failure_does_not_expose_driver_details(monkeypatch):
    monkeypatch.setattr("app.mysql_db.query_connection", lambda: (_ for _ in ()).throw(RuntimeError("password=secret host=db.internal")))
    result = check_connection({"id": "mysql", "backend": "mysql"})
    assert result["ok"] is False
    assert "secret" not in result["message"]
    assert "db.internal" not in result["message"]


def test_legacy_schema_routes_use_safe_error_boundary(client, monkeypatch):
    _override({"subject": "local-dev", "tenant_id": "default", "roles": ["admin"]})
    monkeypatch.setattr(main_module, "datasource_schema", lambda _ds: (_ for _ in ()).throw(RuntimeError("password=secret host=db.internal")))
    response = client.get("/api/v1/data-sources/sqlite-demo/schema")
    assert response.status_code == 502
    assert "secret" not in response.text
    assert "db.internal" not in response.text
