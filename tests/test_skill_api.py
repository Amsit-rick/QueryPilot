from fastapi.testclient import TestClient

from app.api.dependencies import require_manage_access, require_query_access
from app.main import app
from app.skill_runtime import reset_memory_skill_repository


client = TestClient(app)


def test_skill_catalog_and_route_are_exposed_as_authenticated_readonly_api():
    catalog = client.get("/api/v1/skills")
    routed = client.post(
        "/api/v1/skills/route",
        json={"question": "查询销售额变化趋势", "datasource_id": "sqlite-demo"},
    )

    assert catalog.status_code == 200
    assert catalog.json()["read_only"] is True
    assert len(catalog.json()["skills"]) == 5
    assert routed.status_code == 200
    assert routed.json()["selected_skill"]["id"] == "trend-analysis"
    assert routed.json()["datasource_id"] == "sqlite-demo"


def test_skill_route_rejects_unknown_fields_and_oversized_questions():
    extra = client.post(
        "/api/v1/skills/route",
        json={"question": "查询销售额", "admin": True},
    )
    oversized = client.post(
        "/api/v1/skills/route",
        json={"question": "查" * 2001},
    )

    assert extra.status_code == 422
    assert oversized.status_code == 422


def test_skill_route_rechecks_datasource_permission():
    app.dependency_overrides[require_query_access] = lambda: {
        "subject": "viewer",
        "tenant_id": "default",
        "roles": ["viewer"],
    }
    try:
        response = client.post(
            "/api/v1/skills/route",
            json={"question": "查询销售额", "datasource_id": "mysql-main"},
        )
    finally:
        app.dependency_overrides.pop(require_query_access, None)

    assert response.status_code == 403


def test_admin_can_create_canary_publish_preview_and_rollback_skill_versions():
    reset_memory_skill_repository()
    app.dependency_overrides[require_manage_access] = lambda: {
        "subject": "admin-1",
        "tenant_id": "tenant-a",
        "roles": ["admin"],
    }
    app.dependency_overrides[require_query_access] = lambda: {
        "subject": "viewer-7",
        "tenant_id": "tenant-a",
        "roles": ["viewer"],
    }
    payload = {
        "definition": {
            "id": "sales-analysis",
            "version": "1.1.0",
            "name": "销售分析",
            "description": "灰度销售分析",
            "intents": ["sales_query"],
            "trigger_terms": ["销售", "营收"],
            "allowed_tools": ["schema.list", "query.plan", "result.get"],
            "workflow_steps": ["确认口径", "生成只读计划"],
            "allowed_roles": ["viewer", "analyst", "admin"],
            "read_only": True,
            "priority": 70,
        }
    }
    try:
        created = client.post("/api/v1/skills/admin/versions", json=payload)
        version_id = created.json()["id"]
        canary = client.post(
            f"/api/v1/skills/admin/versions/{version_id}/canary",
            json={"percentage": 100},
        )
        catalog = client.get("/api/v1/skills")
        preview = client.post(
            "/api/v1/skills/admin/preview",
            json={"skill_id": "sales-analysis", "subject": "viewer-7"},
        )
        published = client.post(f"/api/v1/skills/admin/versions/{version_id}/publish")
        rolled_back = client.post(f"/api/v1/skills/admin/versions/{version_id}/rollback")
    finally:
        app.dependency_overrides.clear()
        reset_memory_skill_repository()

    assert created.status_code == 200
    assert created.json()["state"] == "draft"
    assert canary.status_code == 200
    assert canary.json()["state"] == "canary"
    assert next(item for item in catalog.json()["skills"] if item["id"] == "sales-analysis")["version"] == "1.1.0"
    assert preview.json()["selected_version"] == "1.1.0"
    assert published.json()["state"] == "active"
    assert rolled_back.status_code == 409


def test_non_manager_cannot_access_skill_release_endpoints():
    response = client.get("/api/v1/skills/admin/versions")
    assert response.status_code in {401, 403}


def test_admin_can_compare_candidate_skill_against_fixed_routing_cases(monkeypatch):
    from app.domain.skill import SkillDefinition
    from app.repositories.memory_skill_repository import InMemorySkillRepository
    from app.services.skill_service import SkillService

    app.dependency_overrides[require_manage_access] = lambda: {
        "subject": "admin-1",
        "tenant_id": "tenant-eval",
        "roles": ["admin"],
    }
    service = SkillService(repository=InMemorySkillRepository())
    monkeypatch.setattr("app.api.skill_routes.get_skill_service", lambda: service)
    candidate = SkillDefinition(
        id="sales-analysis",
        version="1.1.0",
        name="销售分析",
        description="增加毛利问法覆盖",
        intents=("sales_query", "gross_margin_query"),
        trigger_terms=("销售", "销售额", "营收", "成交", "订单金额", "客单价", "毛利"),
        allowed_tools=("schema.list", "query.plan", "result.get"),
        workflow_steps=("确认毛利口径", "生成只读计划"),
        priority=70,
    )
    try:
        response = client.post(
            "/api/v1/skills/admin/evaluate",
            json={"definition": candidate.model_dump(mode="json")},
        )
    finally:
        app.dependency_overrides.pop(require_manage_access, None)

    assert response.status_code == 200
    report = response.json()
    assert report["evaluation_type"] == "skill_routing_only"
    assert report["summary"]["candidate_matches"] > report["summary"]["baseline_matches"]
    assert any(item["id"] == "sales-gross-margin" and item["route_changed"] for item in report["cases"])


def test_non_manager_cannot_evaluate_skill_candidate():
    response = client.post("/api/v1/skills/admin/evaluate", json={
        "definition": {
            "id": "sales-analysis", "version": "1.1.0", "name": "销售分析",
            "description": "安全的销售分析", "intents": ["sales_query"],
            "trigger_terms": ["销售"], "allowed_tools": ["schema.list"],
            "workflow_steps": ["只读查询"],
        }
    })
    assert response.status_code in {401, 403}


def test_admin_can_compare_sql_results_for_skill_versions_without_external_model(monkeypatch):
    from types import SimpleNamespace
    from app.domain.skill import SkillDefinition
    from app.repositories.memory_skill_repository import InMemorySkillRepository
    from app.services.skill_service import SkillService

    app.dependency_overrides[require_manage_access] = lambda: {
        "subject": "admin-1",
        "tenant_id": "tenant-sql-eval",
        "roles": ["admin"],
    }
    monkeypatch.setattr("app.api.skill_routes.get_skill_service", lambda: SkillService(repository=InMemorySkillRepository()))
    runtime = SimpleNamespace(
        adapter="direct", api_key="test-only-key", base_url="https://model.invalid/v1",
        model="fake-model", system_prompt="safe", prompt_version="test-v1",
    )
    monkeypatch.setattr(
        "app.api.skill_routes.get_model_operations_service",
        lambda: SimpleNamespace(resolve_runtime=lambda _tenant: runtime),
    )
    from pathlib import Path
    from app.skill_query_evaluation import load_skill_query_cases
    dataset_path = Path(__file__).resolve().parents[1] / "data" / "skill_query_eval.json"
    _, eval_cases = load_skill_query_cases(dataset_path)
    outputs = {case["question"]: case["gold_sql"] for case in eval_cases}
    calls = []

    async def fake_generate(question, schema, **kwargs):
        calls.append(kwargs["context"])
        if kwargs["context"].get("skill", {}).get("version") == "1.1.0":
            return {"sql": outputs[question]}
        return {"sql": "DELETE FROM sales"}

    monkeypatch.setattr("app.api.skill_routes.generate_sql", fake_generate)
    candidate = SkillDefinition(
        id="sales-analysis", version="1.1.0", name="销售分析",
        description="候选查询质量测试", intents=("sales_query",),
        trigger_terms=("销售", "销售额", "毛利"),
        allowed_tools=("schema.list", "query.plan", "result.get"),
        workflow_steps=("先确认指标口径", "使用聚合结果校验"),
    )
    try:
        response = client.post(
            "/api/v1/skills/admin/evaluate-query-quality",
            json={"definition": candidate.model_dump(mode="json")},
        )
    finally:
        app.dependency_overrides.pop(require_manage_access, None)

    assert response.status_code == 200
    report = response.json()
    assert report["evaluation_type"] == "skill_sql_result_quality"
    assert report["generation_attempts"] == 22
    assert report["summary"]["cases"] == 11
    assert report["summary"]["candidate"]["result_exact_match_rate"] == 1.0
    assert report["summary"]["baseline"]["guardrail_pass_rate"] == 0.0
    assert len(calls) == 22


def test_query_quality_evaluation_requires_configured_model_key_without_leaking_secrets(monkeypatch):
    from types import SimpleNamespace
    from app.repositories.memory_skill_repository import InMemorySkillRepository
    from app.services.skill_service import SkillService

    app.dependency_overrides[require_manage_access] = lambda: {
        "subject": "admin-1",
        "tenant_id": "tenant-no-key",
        "roles": ["admin"],
    }
    monkeypatch.setattr("app.api.skill_routes.get_skill_service", lambda: SkillService(repository=InMemorySkillRepository()))
    monkeypatch.setattr(
        "app.api.skill_routes.get_model_operations_service",
        lambda: SimpleNamespace(resolve_runtime=lambda _tenant: SimpleNamespace(api_key="", model="secret-model")),
    )
    try:
        response = client.post(
            "/api/v1/skills/admin/evaluate-query-quality",
            json={"definition": {
                "id": "sales-analysis", "version": "1.1.0", "name": "销售分析",
                "description": "测试", "intents": ["sales_query"], "trigger_terms": ["销售"],
                "allowed_tools": ["schema.list"], "workflow_steps": ["只读查询"],
            }},
        )
    finally:
        app.dependency_overrides.pop(require_manage_access, None)

    assert response.status_code == 409
    assert "API Key" in response.json()["detail"]
    assert "secret-model" not in response.text


def test_non_manager_cannot_run_skill_sql_quality_evaluation():
    response = client.post(
        "/api/v1/skills/admin/evaluate-query-quality",
        json={"definition": {"id": "sales-analysis"}},
    )
    assert response.status_code in {401, 403}
