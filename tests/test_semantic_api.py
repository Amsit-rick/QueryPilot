from fastapi.testclient import TestClient

from app import main as main_module
from app.auth import require_identity
from app.semantic_runtime import reset_memory_semantics


def _identity(value):
    main_module.app.dependency_overrides[require_identity] = lambda: value


def test_admin_semantic_crud_and_search_respect_role_schema():
    reset_memory_semantics()
    _identity({"subject": "1", "tenant_id": "default", "roles": ["admin"]})
    client = TestClient(main_module.app)
    try:
        created = client.post("/api/v1/semantics", json={"datasource_id": "sqlite-demo", "kind": "metric", "name": "销售额", "definition": {"formula": "SUM(amount)", "tables": ["sales"], "columns": ["amount"]}})
        assert created.status_code == 200
        entry_id = created.json()["id"]
        assert client.patch(f"/api/v1/semantics/{entry_id}", json={"description": "含税销售额"}).json()["version"] == 2
        assert len(client.get(f"/api/v1/semantics/{entry_id}/history").json()) == 2
        assert client.post("/api/v1/semantics", json={"datasource_id": "sqlite-demo", "kind": "metric", "name": "错误指标", "definition": {"tables": ["missing"], "columns": ["amount"]}}).status_code == 422

        _identity({"subject": "viewer", "tenant_id": "default", "roles": ["viewer"]})
        assert client.get("/api/v1/semantics", params={"datasource_id": "sqlite-demo"}).status_code == 403
        assert client.get(f"/api/v1/semantics/{entry_id}/history").status_code == 403
        assert client.get("/api/v1/semantics/search", params={"datasource_id": "sqlite-demo", "q": "销售额"}).json()["entries"] == []
        assert client.post("/api/v1/semantics", json={"datasource_id": "sqlite-demo", "kind": "metric", "name": "x", "definition": {"tables": ["sales"], "columns": ["region"]}}).status_code == 403
    finally:
        main_module.app.dependency_overrides.clear()
        reset_memory_semantics()


def test_query_context_includes_only_authorized_semantics():
    reset_memory_semantics()
    client = TestClient(main_module.app)
    _identity({"subject": "1", "tenant_id": "default", "roles": ["admin"]})
    client.post("/api/v1/semantics", json={"datasource_id": "sqlite-demo", "kind": "alias", "name": "客户称呼", "definition": {"target_table": "customers", "target_column": "name", "tables": ["customers"], "columns": ["name"]}})
    client.post("/api/v1/semantics", json={"datasource_id": "sqlite-demo", "kind": "alias", "name": "销售区域", "definition": {"target_table": "sales", "target_column": "region", "tables": ["sales"], "columns": ["region"]}})
    try:
        _identity({"subject": "viewer", "tenant_id": "default", "roles": ["viewer"]})
        body = client.get("/api/v1/semantics/search", params={"datasource_id": "sqlite-demo", "q": "客户称呼 销售区域"}).json()
        assert [item["name"] for item in body["entries"]] == ["销售区域"]
    finally:
        main_module.app.dependency_overrides.clear()
        reset_memory_semantics()


def test_query_records_semantic_versions_in_task_timeline():
    reset_memory_semantics()
    client = TestClient(main_module.app)
    _identity({"subject": "1", "tenant_id": "default", "roles": ["admin"]})
    created = client.post("/api/v1/semantics", json={"datasource_id": "sqlite-demo", "kind": "metric", "name": "销售额", "definition": {"formula": "SUM(amount)", "tables": ["sales"], "columns": ["amount"]}}).json()
    try:
        result = client.post("/api/v1/query", json={"question": "查询销售额", "sql": "SELECT amount FROM sales", "datasource": "sqlite-demo"}).json()
        detail = client.get(f"/api/v1/tasks/{result['task_id']}").json()
        semantic_step = next(step for step in detail["steps"] if step["kind"] == "resolve_semantics")
        assert semantic_step["output"]["semantic_entries"] == 1
        assert semantic_step["output"]["semantic_versions"] == {created["logical_id"]: 1}
        assert semantic_step["output"]["semantic_refs"][0]["definition"]["formula"] == "SUM(amount)"
    finally:
        main_module.app.dependency_overrides.clear()
        reset_memory_semantics()


def test_sql_example_is_available_to_admin_and_searchable():
    reset_memory_semantics()
    client = TestClient(main_module.app)
    _identity({"subject": "1", "tenant_id": "default", "roles": ["admin"]})
    try:
        response = client.post(
            "/api/v1/semantics",
            json={
                "datasource_id": "sqlite-demo",
                "kind": "sql_example",
                "name": "按区域统计销售额",
                "definition": {
                    "question": "各区域销售额是多少？",
                    "sql": "SELECT region, SUM(amount) AS total FROM sales GROUP BY region",
                    "tables": ["sales"],
                    "columns": ["region", "amount"],
                },
            },
        )
        assert response.status_code == 200
        assert response.json()["kind"] == "sql_example"
        search = client.get("/api/v1/semantics/search", params={"datasource_id": "sqlite-demo", "q": "各区域销售额"}).json()["entries"]
        assert search[0]["kind"] == "sql_example"
        assert search[0]["match_score"] > 0
        assert "question" in search[0]["match_reasons"]
    finally:
        main_module.app.dependency_overrides.clear()
        reset_memory_semantics()


def test_value_dictionary_api_is_admin_managed_and_permission_filtered():
    reset_memory_semantics()
    client = TestClient(main_module.app)
    _identity({"subject": "1", "tenant_id": "default", "roles": ["admin"]})
    try:
        response = client.post(
            "/api/v1/semantics",
            json={
                "datasource_id": "sqlite-demo",
                "kind": "value_dictionary",
                "name": "销售区域值词典",
                "definition": {
                    "target_table": "sales",
                    "target_column": "region",
                    "tables": ["sales"],
                    "columns": ["region"],
                    "values": [{"value": "EAST", "aliases": ["华东"]}],
                },
            },
        )
        assert response.status_code == 200
        assert response.json()["kind"] == "value_dictionary"

        _identity({"subject": "viewer", "tenant_id": "default", "roles": ["viewer"]})
        search = client.get(
            "/api/v1/semantics/search",
            params={"datasource_id": "sqlite-demo", "q": "华东"},
        ).json()["entries"]
        assert search == []
    finally:
        main_module.app.dependency_overrides.clear()
        reset_memory_semantics()
