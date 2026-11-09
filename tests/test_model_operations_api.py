import os

from fastapi.testclient import TestClient

import app.main as main_module
from app.auth import require_identity
from app.model_operations_runtime import reset_memory_model_operations


client = TestClient(main_module.app)


def identity(value):
    main_module.app.dependency_overrides[require_identity] = lambda: value


def setup_function():
    reset_memory_model_operations()
    main_module.app.dependency_overrides.clear()


def teardown_function():
    reset_memory_model_operations()
    main_module.app.dependency_overrides.clear()


def test_admin_provider_api_never_accepts_or_returns_inline_secret(monkeypatch):
    monkeypatch.setenv("TENANT_MODEL_API_KEY", "do-not-return-this-secret")
    identity({"subject": "1", "tenant_id": "tenant-a", "roles": ["admin"]})
    rejected = client.post("/api/v1/model-ops/providers", json={"name": "bad", "adapter": "direct", "base_url": "https://api.example.com", "model": "m", "api_key_env": "TENANT_MODEL_API_KEY", "api_key": "inline-secret"})
    assert rejected.status_code == 422

    created = client.post("/api/v1/model-ops/providers", json={"name": "Primary", "adapter": "direct", "base_url": "https://api.example.com", "model": "model-a", "api_key_env": "TENANT_MODEL_API_KEY", "is_default": True})
    assert created.status_code == 200
    payload = created.json()
    assert payload["credential_configured"] is True
    assert payload["api_key_env"] == "TENANT_MODEL_API_KEY"
    assert "do-not-return-this-secret" not in created.text
    assert "do-not-return-this-secret" not in client.get("/api/v1/model-ops/providers").text


def test_model_operations_api_requires_admin_and_isolates_tenants():
    identity({"subject": "viewer", "tenant_id": "tenant-a", "roles": ["viewer"]})
    assert client.get("/api/v1/model-ops/providers").status_code == 403

    identity({"subject": "1", "tenant_id": "tenant-a", "roles": ["admin"]})
    created = client.post("/api/v1/model-ops/providers", json={"name": "Tenant A", "adapter": "direct", "base_url": "https://api.example.com", "model": "model-a", "api_key_env": "TENANT_A_MODEL_KEY"})
    assert created.status_code == 200
    identity({"subject": "2", "tenant_id": "tenant-b", "roles": ["admin"]})
    assert client.get("/api/v1/model-ops/providers").json() == []
    assert client.patch(f"/api/v1/model-ops/providers/{created.json()['id']}", json={"enabled": False}).status_code == 404


def test_prompt_versions_activate_and_runtime_metadata_is_sanitized(monkeypatch):
    monkeypatch.setenv("TENANT_MODEL_API_KEY", "runtime-secret")
    identity({"subject": "1", "tenant_id": "tenant-a", "roles": ["admin"]})
    client.post("/api/v1/model-ops/providers", json={"name": "Primary", "adapter": "direct", "base_url": "https://api.example.com", "model": "model-a", "api_key_env": "TENANT_MODEL_API_KEY"})
    first = client.post("/api/v1/model-ops/prompts", json={"name": "sql_generation", "system_prompt": "第一版"}).json()
    second = client.post("/api/v1/model-ops/prompts", json={"name": "sql_generation", "system_prompt": "第二版"}).json()
    assert first["active"] is True
    assert second["active"] is False
    assert client.post(f"/api/v1/model-ops/prompts/{second['id']}/activate").status_code == 200
    runtime = client.get("/api/v1/model-ops/runtime").json()
    assert runtime["prompt_version"] == "sql_generation-v2"
    assert runtime["model"] == "model-a"
    assert runtime["credential_configured"] is True
    assert "runtime-secret" not in str(runtime)


def test_explicit_sql_still_runs_when_registered_model_secret_is_missing():
    identity({"subject": "1", "tenant_id": "tenant-a", "roles": ["admin"]})
    client.post("/api/v1/model-ops/providers", json={"name": "Offline", "adapter": "direct", "base_url": "https://api.invalid", "model": "offline-model", "api_key_env": "MISSING_MODEL_KEY"})
    response = client.post("/api/v1/query", json={"question": "显式查询", "sql": "SELECT region FROM sales", "datasource": "sqlite-demo"})
    assert response.status_code == 200
    assert response.json()["row_count"] > 0
    detail = client.get(f"/api/v1/tasks/{response.json()['task_id']}").json()
    assert detail["steps"][0]["output"]["model"] == "offline-model"
    assert detail["steps"][0]["output"]["prompt_version"] == "sql-generator-v2"
