import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import require_query_access
from app.main import app
from app.services.read_only_integration_service import get_read_only_integration_service
from app.task_runtime import get_task_service


client = TestClient(app)


@pytest.fixture
def viewer_client():
    app.dependency_overrides[require_query_access] = lambda: {
        "subject": "viewer",
        "tenant_id": "default",
        "roles": ["viewer"],
    }
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_query_access, None)


def test_read_only_tool_manifest_is_stable_and_has_no_execution_tool():
    response = client.get("/api/v1/integrations/read-only/tools")

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 1
    assert payload["read_only"] is True
    assert {tool["name"] for tool in payload["tools"]} == {
        "schema.list",
        "query.plan",
        "task.status",
        "result.get",
    }


def test_embedded_service_uses_the_same_readonly_contract():
    result = get_read_only_integration_service().invoke(
        {"subject": "embedded", "tenant_id": "default", "roles": ["analyst"]},
        "schema.list",
        {"datasource_id": "sqlite-demo"},
    )

    assert result["version"] == 1
    assert result["read_only"] is True
    assert "sales" in result["result"]["tables"]


def test_schema_tool_reapplies_role_column_policy(viewer_client):
    response = viewer_client.post(
        "/api/v1/integrations/read-only/call",
        json={"tool": "schema.list", "arguments": {"datasource_id": "sqlite-demo"}},
    )

    assert response.status_code == 200
    assert response.json()["result"]["tables"] == {"sales": ["region"]}


def test_query_plan_validates_readonly_sql_without_executing():
    response = client.post(
        "/api/v1/integrations/read-only/call",
        json={
            "tool": "query.plan",
            "request_id": "plan-1",
            "arguments": {
                "question": "查询区域销售额",
                "datasource_id": "sqlite-demo",
                "sql": "SELECT region, SUM(amount) AS total FROM sales GROUP BY region",
            },
        },
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["validation"] == "passed"
    assert result["execution_delegated"] is True
    assert result["sql"].startswith("SELECT region")

    rejected = client.post(
        "/api/v1/integrations/read-only/call",
        json={
            "tool": "query.plan",
            "arguments": {
                "question": "删除测试数据",
                "datasource_id": "sqlite-demo",
                "sql": "DELETE FROM sales",
            },
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "E_READONLY_VIOLATION"


def test_task_and_result_tools_are_owner_scoped():
    task = get_task_service().create(
        question="查询销售额",
        tenant_id="default",
        owner_id="owner-1",
        datasource_id="sqlite-demo",
    )

    app.dependency_overrides[require_query_access] = lambda: {
        "subject": "owner-1",
        "tenant_id": "default",
        "roles": ["analyst"],
    }
    try:
        status = client.post(
            "/api/v1/integrations/read-only/call",
            json={"tool": "task.status", "arguments": {"task_id": task.id}},
        )
        result = client.post(
            "/api/v1/integrations/read-only/call",
            json={"tool": "result.get", "arguments": {"task_id": task.id}},
        )
        assert status.status_code == 200
        assert status.json()["result"]["status"] == "draft"
        assert result.status_code == 200
        assert result.json()["result"]["artifacts"] == []
    finally:
        app.dependency_overrides.pop(require_query_access, None)

    app.dependency_overrides[require_query_access] = lambda: {
        "subject": "other-user",
        "tenant_id": "default",
        "roles": ["analyst"],
    }
    try:
        forbidden = client.post(
            "/api/v1/integrations/read-only/call",
            json={"tool": "task.status", "arguments": {"task_id": task.id}},
        )
        assert forbidden.status_code == 404
        assert forbidden.json()["detail"]["code"] == "E_TASK_NOT_FOUND"
    finally:
        app.dependency_overrides.pop(require_query_access, None)


def test_unknown_tool_and_extra_request_fields_are_rejected():
    unknown = client.post(
        "/api/v1/integrations/read-only/call",
        json={"tool": "query.execute", "arguments": {}},
    )
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["code"] == "E_INTEGRATION_TOOL_NOT_FOUND"

    extra = client.post(
        "/api/v1/integrations/read-only/call",
        json={"tool": "schema.list", "arguments": {}, "secret": "nope"},
    )
    assert extra.status_code == 422
