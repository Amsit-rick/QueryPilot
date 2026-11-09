import json

from fastapi.testclient import TestClient

from app.api.dependencies import require_manage_access
from app.main import app
from app.domain.reflection_rollout import ReflectionRolloutPolicy
from app.reflection_rollout_runtime import get_reflection_rollout_service, reset_memory_reflection_rollout_repository
from app.config import settings


client = TestClient(app)


def _admin(tenant="tenant-rollout"):
    return {"subject": "admin-1", "tenant_id": tenant, "roles": ["admin"]}


def _gate_payload():
    return {
        "format": "query-pilot-reflection-release-gate",
        "version": 1,
        "passed": True,
        "reason_codes": [],
        "inputs": {
            "action": {"dataset_version": "reflection-eval-v1", "passed": True, "sha256": "a" * 64},
            "result": {"dataset_version": "reflection-result-eval-v1", "passed": True, "sha256": "b" * 64},
            "security": {"dataset_version": "reflection-security-eval-v1", "passed": True, "sha256": "c" * 64},
        },
        "metrics": {"final_result_accuracy": 1.0, "security_permission_bypasses": 0, "sensitive_value_leaks": 0, "dangerous_actions": 0},
    }


def test_admin_can_create_canary_publish_preview_and_rollback_reflection_policy(tmp_path, monkeypatch):
    reset_memory_reflection_rollout_repository()
    app.dependency_overrides[require_manage_access] = _admin
    monkeypatch.setattr("app.reflection_rollout_runtime.settings.database_backend", "sqlite")
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps(_gate_payload()), encoding="utf-8")
    monkeypatch.setattr("app.api.reflection_rollout_routes.settings.agent_reflection_release_gate_path", str(gate))
    try:
        first = client.post("/api/v1/reflection-rollouts/admin/versions", json={"policy":{"version":"1.0.0","mode":"shadow"}})
        first_id = first.json()["id"]
        published_first = client.post(f"/api/v1/reflection-rollouts/admin/versions/{first_id}/publish")
        second = client.post("/api/v1/reflection-rollouts/admin/versions", json={"policy":{"version":"2.0.0","mode":"enforced"}})
        second_id = second.json()["id"]
        canary = client.post(f"/api/v1/reflection-rollouts/admin/versions/{second_id}/canary", json={"percentage":100})
        preview = client.post("/api/v1/reflection-rollouts/admin/preview", json={"subject":"analyst-7"})
        published_second = client.post(f"/api/v1/reflection-rollouts/admin/versions/{second_id}/publish")
        rolled_back = client.post(f"/api/v1/reflection-rollouts/admin/versions/{second_id}/rollback")
        versions = client.get("/api/v1/reflection-rollouts/admin/versions")
    finally:
        app.dependency_overrides.clear()
        reset_memory_reflection_rollout_repository()

    assert first.status_code == 200 and first.json()["state"] == "draft"
    assert published_first.json()["state"] == "active"
    assert second.status_code == 200
    assert canary.json()["state"] == "canary"
    assert preview.json()["policy_version"] == "2.0.0"
    assert preview.json()["effective_mode"] == "enforced"
    assert published_second.json()["state"] == "active"
    assert rolled_back.json()["policy"]["version"] == "1.0.0"
    assert len(versions.json()) == 2


def test_non_manager_cannot_manage_reflection_rollouts():
    response = client.get("/api/v1/reflection-rollouts/admin/versions")
    assert response.status_code in {401, 403}


def test_query_runtime_applies_active_tenant_reflection_policy(tmp_path, monkeypatch):
    reset_memory_reflection_rollout_repository()
    monkeypatch.setattr("app.reflection_rollout_runtime.settings.database_backend", "sqlite")
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps(_gate_payload()), encoding="utf-8")
    monkeypatch.setattr(settings, "agent_reflection_release_gate_path", str(gate))
    monkeypatch.setattr(settings, "agent_reflection_mode", "shadow")
    service = get_reflection_rollout_service()
    policy = service.create_version(
        tenant_id="default",
        actor_id="admin",
        policy=ReflectionRolloutPolicy(version="9.0.0", mode="enforced"),
    )
    service.publish(policy.id, tenant_id="default", actor_id="admin")
    try:
        response = client.post(
            "/api/v1/query",
            json={
                "question": "销售额最高的前 5 个区域",
                "sql": "SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region",
            },
        )
    finally:
        reset_memory_reflection_rollout_repository()

    assert response.status_code == 200
    assert response.json()["error"]["code"] == "CALLER_SQL_RESULT_REJECTED"


def test_admin_health_evaluation_rolls_back_unhealthy_active_version(monkeypatch):
    reset_memory_reflection_rollout_repository()
    app.dependency_overrides[require_manage_access] = _admin
    monkeypatch.setattr("app.reflection_rollout_runtime.settings.database_backend", "sqlite")
    try:
        first = client.post(
            "/api/v1/reflection-rollouts/admin/versions",
            json={"policy": {"version": "1.0.0", "mode": "shadow"}},
        ).json()
        client.post(f"/api/v1/reflection-rollouts/admin/versions/{first['id']}/publish")
        second = client.post(
            "/api/v1/reflection-rollouts/admin/versions",
            json={
                "policy": {
                    "version": "2.0.0",
                    "mode": "enforced",
                    "max_error_acceptance_rate": 0.01,
                }
            },
        ).json()
        client.post(f"/api/v1/reflection-rollouts/admin/versions/{second['id']}/publish")

        response = client.post(
            f"/api/v1/reflection-rollouts/admin/versions/{second['id']}/evaluate-health",
            json={
                "error_acceptance_rate": 0.02,
                "invalid_retry_rate": 0,
                "p95_latency_ms": 900,
                "security_permission_bypasses": 0,
            },
        )
    finally:
        app.dependency_overrides.clear()
        reset_memory_reflection_rollout_repository()

    assert response.status_code == 200
    assert response.json()["rolled_back"] is True
    assert response.json()["active_version"] == "1.0.0"
    assert response.json()["reason_codes"] == ["ERROR_ACCEPTANCE_RATE_EXCEEDED"]

