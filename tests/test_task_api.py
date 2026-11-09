from fastapi.testclient import TestClient
from app.main import app


def test_task_api_create_list_and_status():
    client = TestClient(app)
    created = client.post("/api/v1/tasks", json={"question": "查询销售额", "datasource": "sqlite-demo"})
    assert created.status_code == 200
    task = created.json()
    assert task["status"] == "draft"
    listed = client.get("/api/v1/tasks")
    assert listed.status_code == 200 and listed.json()[0]["id"] == task["id"]
    updated = client.patch(f"/api/v1/tasks/{task['id']}/status", json={"status": "planning"})
    assert updated.status_code == 200 and updated.json()["status"] == "planning"
    detail = client.get(f"/api/v1/tasks/{task['id']}")
    assert detail.status_code == 200
    assert detail.json()["steps"] == []
    assert [event["kind"] for event in detail.json()["events"]] == ["task_created", "status_changed"]
    assert detail.json()["artifacts"] == []


def test_task_detail_is_isolated_by_owner():
    from app.auth import require_identity

    app.dependency_overrides[require_identity] = lambda: {"subject": "owner-a", "tenant_id": "tenant-a", "roles": ["analyst"]}
    try:
        client = TestClient(app)
        task_id = client.post("/api/v1/tasks", json={"question": "查询销售额", "datasource": "sqlite-demo"}).json()["id"]
        app.dependency_overrides[require_identity] = lambda: {"subject": "owner-b", "tenant_id": "tenant-a", "roles": ["analyst"]}
        assert client.get(f"/api/v1/tasks/{task_id}").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_generic_status_endpoint_cannot_reopen_completed_task():
    client = TestClient(app)
    completed = client.post("/api/v1/query", json={"question": "查询", "sql": "SELECT region FROM sales"}).json()

    response = client.patch(f"/api/v1/tasks/{completed['task_id']}/status", json={"status": "planning"})

    assert response.status_code == 409
    assert "不能从 completed 转换到 planning" in response.text
