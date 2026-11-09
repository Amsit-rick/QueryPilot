import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import require_manage_access, require_query_access
from app.main import app


client = TestClient(app)


@pytest.fixture
def admin_client():
    app.dependency_overrides[require_manage_access] = lambda: {"subject": "admin", "tenant_id": "default", "roles": ["admin"]}
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_manage_access, None)


@pytest.fixture
def viewer_client():
    app.dependency_overrides[require_query_access] = lambda: {"subject": "viewer", "tenant_id": "default", "roles": ["viewer"]}
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_query_access, None)


def test_gateway_api_lists_schema_health_and_preview():
    listed = client.get("/api/v1/data-gateways")
    assert listed.status_code == 200
    sqlite = next(item for item in listed.json() if item["id"] == "sqlite-demo")
    assert sqlite["dialect"] == "sqlite"
    assert sqlite["capabilities"]["readonly"] is True
    assert sqlite["capabilities"]["cancel"] is False

    schema = client.get("/api/v1/data-gateways/sqlite-demo/schema")
    assert schema.status_code == 200
    assert "sales" in schema.json()["tables"]

    health = client.get("/api/v1/data-gateways/sqlite-demo/health")
    assert health.status_code == 200
    assert health.json()["status"] == "healthy"

    preview = client.get("/api/v1/data-gateways/sqlite-demo/preview", params={"table": "sales", "limit": 2})
    assert preview.status_code == 200
    assert preview.json()["row_count"] <= 2
    assert preview.json()["columns"]


def test_gateway_config_failure_is_not_returned_as_unhandled_500(monkeypatch):
    from app.domain.data_gateway import GatewayError

    def broken_registry(_datasources):
        raise GatewayError("E_GATEWAY_CONFIG", "数据网关配置无效", operation="configure")

    monkeypatch.setattr("app.api.data_gateway_routes.build_gateway_registry", broken_registry)
    response = client.get("/api/v1/data-gateways")

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "E_GATEWAY_CONFIG",
        "message": "数据网关配置无效",
        "operation": "configure",
        "retryable": False,
    }


def test_gateway_schema_failure_returns_retryable_bad_gateway(monkeypatch):
    from app.domain.data_gateway import GatewayError

    def broken_schema(self, _identity, _gateway_id):
        raise GatewayError("E_GATEWAY_SCHEMA", "数据网关 schema 操作失败", operation="schema", retryable=True)

    monkeypatch.setattr("app.services.data_gateway_service.DataGatewayService.schema", broken_schema)
    response = client.get("/api/v1/data-gateways/sqlite-demo/schema")

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "E_GATEWAY_SCHEMA"
    assert response.json()["detail"]["retryable"] is True


def test_gateway_api_rejects_unknown_table_and_unsupported_cancel(admin_client):
    missing = client.get("/api/v1/data-gateways/sqlite-demo/preview", params={"table": "users"})
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "E_GATEWAY_TABLE"

    cancelled = admin_client.post("/api/v1/data-gateways/sqlite-demo/cancel", json={"operation_id": "op-1"})
    assert cancelled.status_code == 409
    assert cancelled.json()["detail"]["code"] == "E_GATEWAY_UNSUPPORTED"


def test_gateway_admin_query_is_readonly_and_schema_bound(admin_client):
    result = admin_client.post(
        "/api/v1/data-gateways/sqlite-demo/query",
        json={"sql": "SELECT region, amount FROM sales ORDER BY id", "max_rows": 2},
    )
    assert result.status_code == 200
    assert result.json()["columns"] == ["region", "amount"]

    rejected = admin_client.post(
        "/api/v1/data-gateways/sqlite-demo/query",
        json={"sql": "SELECT password_hash FROM users"},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "E_HALLUCINATED_FIELD"


def test_gateway_preview_projects_only_role_allowed_columns(viewer_client):
    schema = viewer_client.get("/api/v1/data-gateways/sqlite-demo/schema").json()
    assert schema["tables"] == {"sales": ["region"]}
    preview = viewer_client.get("/api/v1/data-gateways/sqlite-demo/preview", params={"table": "sales"})
    assert preview.status_code == 200
    assert preview.json()["columns"] == ["region"]


def test_query_task_records_gateway_datasource_and_dialect():
    response = client.post("/api/v1/query", json={"question": "查询销售额", "sql": "SELECT region, amount FROM sales"})
    assert response.status_code == 200
    detail = client.get(f"/api/v1/tasks/{response.json()['task_id']}").json()
    understand = next(step for step in detail["steps"] if step["kind"] == "understand_question")
    assert understand["output"]["gateway_id"] == "sqlite-demo"
    assert understand["output"]["datasource_id"] == "sqlite-demo"
    assert understand["output"]["dialect"] == "sqlite"
