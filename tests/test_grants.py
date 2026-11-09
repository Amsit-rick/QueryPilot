import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.auth import require_identity
from app.policy import apply_row_filter
from app.policy import get_role_policy, update_role_policy


@pytest.fixture()
def client(monkeypatch):
    app = main_module.app
    app.dependency_overrides.clear()
    monkeypatch.setattr(main_module, "get_user_by_id", lambda user_id: {"id": user_id, "tenant_id": "default"})
    yield TestClient(app)
    app.dependency_overrides.clear()


def _override(identity):
    main_module.app.dependency_overrides[require_identity] = lambda: identity


def test_apply_row_filter_appends_where():
    sql, params = apply_row_filter("SELECT region FROM sales", "region", ["华东"])
    assert "region IN (:grant0)" in sql
    assert params == {"grant0": "华东"}


def test_apply_row_filter_appends_to_existing_where():
    sql, params = apply_row_filter("SELECT region FROM sales WHERE amount > 100", "region", ["华东", "华南"])
    assert "amount > 100" in sql
    assert "region IN (:grant0, :grant1)" in sql
    assert params == {"grant0": "华东", "grant1": "华南"}


def test_apply_row_filter_rejects_unsafe_column():
    sql, params = apply_row_filter("SELECT region FROM sales", "region; DROP TABLE sales", ["x"])
    assert sql == "SELECT region FROM sales"
    assert params == {}


def test_row_filter_restricts_query_results(client, monkeypatch):
    _override({"subject": "1", "tenant_id": "default", "roles": ["analyst"]})
    monkeypatch.setattr(main_module, "get_user_grants", lambda user_id: [
        {"id": 1, "datasource_id": "default", "scope_column": "region", "scope_value": "华东"}
    ])
    body = client.post("/api/v1/query", json={"question": "查询", "sql": "SELECT region, amount FROM sales"}).json()
    assert body["row_count"] >= 1
    assert all(row[0] == "华东" for row in body["rows"])


def test_no_grants_means_no_row_filter(client, monkeypatch):
    _override({"subject": "1", "tenant_id": "default", "roles": ["analyst"]})
    monkeypatch.setattr(main_module, "get_user_grants", lambda user_id: [])
    body = client.post("/api/v1/query", json={"question": "查询", "sql": "SELECT region FROM sales"}).json()
    assert body["row_count"] >= 3


def test_admin_not_row_filtered(client):
    _override({"subject": "1", "tenant_id": "default", "roles": ["admin"]})
    body = client.post("/api/v1/query", json={"question": "查询", "sql": "SELECT region FROM sales"}).json()
    assert body["row_count"] >= 3


def test_list_grants_endpoint(client, monkeypatch):
    _override({"subject": "1", "tenant_id": "default", "roles": ["admin"]})
    monkeypatch.setattr(main_module, "get_user_grants", lambda user_id: [
        {"id": 1, "datasource_id": "default", "scope_column": "region", "scope_value": "华东"}
    ])
    body = client.get("/api/v1/admin/users/1/grants").json()
    assert body[0]["scope_value"] == "华东"


def test_create_grant_endpoint(client, monkeypatch):
    _override({"subject": "1", "tenant_id": "default", "roles": ["admin"]})
    calls = []
    monkeypatch.setattr(main_module, "add_user_grant", lambda *args: calls.append(args) or 9)
    monkeypatch.setattr(main_module, "datasource_schema", lambda ds: {"sales": {"region", "amount"}})
    resp = client.post("/api/v1/admin/users/2/grants", json={"datasource_id": "default", "scope_column": "region", "scope_value": "华南"})
    assert resp.status_code == 200 and resp.json()["id"] == 9
    assert calls == [(2, "default", "region", "华南")]


def test_create_grant_rejects_unsafe_column(client):
    _override({"subject": "1", "tenant_id": "default", "roles": ["admin"]})
    resp = client.post("/api/v1/admin/users/2/grants", json={"datasource_id": "default", "scope_column": "region;drop", "scope_value": "x"})
    assert resp.status_code == 400


def test_create_grant_rejects_unknown_datasource(client, monkeypatch):
    _override({"subject": "1", "tenant_id": "default", "roles": ["admin"]})
    monkeypatch.setattr(main_module, "add_user_grant", lambda *args: pytest.fail("must not persist"))
    resp = client.post("/api/v1/admin/users/2/grants", json={"datasource_id": "missing", "scope_column": "region", "scope_value": "华南"})
    assert resp.status_code == 404


def test_create_grant_rejects_unknown_column(client, monkeypatch):
    _override({"subject": "1", "tenant_id": "default", "roles": ["admin"]})
    monkeypatch.setattr(main_module, "datasource_schema", lambda ds: {"sales": {"region"}})
    resp = client.post("/api/v1/admin/users/2/grants", json={"datasource_id": "sqlite-demo", "scope_column": "not_a_column", "scope_value": "x"})
    assert resp.status_code == 400


def test_delete_grant_endpoint(client, monkeypatch):
    _override({"subject": "1", "tenant_id": "default", "roles": ["admin"]})
    calls = []
    monkeypatch.setattr(main_module, "delete_user_grant", lambda grant_id, tenant_id: calls.append((grant_id, tenant_id)))
    resp = client.delete("/api/v1/admin/grants/7")
    assert resp.status_code == 200
    assert calls == [(7, "default")]


def test_grants_require_manage_permission(client):
    _override({"subject": "1", "tenant_id": "default", "roles": ["analyst"]})
    assert client.get("/api/v1/admin/users/1/grants").status_code == 403
    assert client.post("/api/v1/admin/users/1/grants", json={"scope_column": "region", "scope_value": "华东"}).status_code == 403


def test_grant_management_rejects_cross_tenant_user(client, monkeypatch):
    _override({"subject": "1", "tenant_id": "tenant-a", "roles": ["admin"]})
    monkeypatch.setattr(main_module, "get_user_by_id", lambda user_id: {"id": user_id, "tenant_id": "tenant-b"})
    assert client.get("/api/v1/admin/users/9/grants").status_code == 404
    assert client.post("/api/v1/admin/users/9/grants", json={"scope_column": "region", "scope_value": "华东"}).status_code == 404


def test_row_filter_does_not_cross_datasource(client, monkeypatch):
    _override({"subject": "1", "tenant_id": "default", "roles": ["analyst"]})
    monkeypatch.setattr(main_module, "get_user_grants", lambda user_id: [
        {"id": 1, "datasource_id": "mysql-main", "scope_column": "region", "scope_value": "华东"}
    ])
    body = client.post("/api/v1/query", json={"question": "查询", "datasource": "sqlite-demo", "sql": "SELECT region, amount FROM sales"}).json()
    assert body["row_count"] >= 3


def test_role_policy_update_validates_and_persists_atomically(tmp_path, monkeypatch):
    policy_path = tmp_path / "access_policy.json"
    monkeypatch.setattr(main_module.settings, "policy_path", str(policy_path))
    available = {"sales": {"region", "amount"}, "customers": {"name"}}
    saved = update_role_policy("sqlite-demo", "viewer", {"tables": ["sales"], "columns": {"sales": ["region"]}, "mask": {}}, available)
    assert saved["tables"] == ["sales"]
    assert get_role_policy("sqlite-demo", "viewer")["columns"] == {"sales": ["region"]}
    with pytest.raises(ValueError):
        update_role_policy("sqlite-demo", "viewer", {"tables": ["missing"], "columns": "*", "mask": {}}, available)


def test_role_policy_api_round_trip(client, monkeypatch, tmp_path):
    _override({"subject": "1", "tenant_id": "default", "roles": ["admin"]})
    monkeypatch.setattr(main_module.settings, "policy_path", str(tmp_path / "policy.json"))
    monkeypatch.setattr(main_module, "datasource_schema", lambda ds: {"sales": {"region", "amount"}})
    response = client.put("/api/v1/admin/role-policies", json={"datasource_id": "sqlite-demo", "role": "viewer", "tables": ["sales"], "columns": {"sales": ["region"]}, "mask": {"region": "partial"}})
    assert response.status_code == 200
    loaded = client.get("/api/v1/admin/role-policies", params={"datasource_id": "sqlite-demo", "role": "viewer"})
    assert loaded.status_code == 200 and loaded.json()["mask"] == {"region": "partial"}
