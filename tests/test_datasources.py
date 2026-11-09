import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.auth import require_identity
from app.config import settings
from app.datasources import (
    datasource_schema,
    execute_datasource,
    list_data_sources_for,
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


def test_viewer_only_sees_sqlite():
    ids = [d["id"] for d in list_data_sources_for({"roles": ["viewer"]})]
    assert ids == ["sqlite-demo"]


def test_analyst_and_admin_see_both():
    assert [d["id"] for d in list_data_sources_for({"roles": ["analyst"]})] == ["sqlite-demo", "mysql-main"]
    assert [d["id"] for d in list_data_sources_for({"roles": ["admin"]})] == ["sqlite-demo", "mysql-main"]


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
