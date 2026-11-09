import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.auth import require_identity
from app.config import settings
from app.mcp_registry_runtime import reset_memory_mcp_registry
from app.repositories.memory_mcp_registry_repository import InMemoryMcpRegistryRepository
from app.services.mcp_invocation_service import McpInvocationService, McpToolDescriptor
from app.services.mcp_registry_service import McpRegistryService
from app.services.mcp_runtime_guard import McpRuntimeGuard
import app.api.mcp_registry_routes as mcp_routes


client = TestClient(main_module.app)


@pytest.fixture(autouse=True)
def isolated_audit_log(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "audit_log_path", str(tmp_path / "audit.jsonl"))


def identity(value):
    main_module.app.dependency_overrides[require_identity] = lambda: value


def setup_function():
    reset_memory_mcp_registry()
    main_module.app.dependency_overrides.clear()


def teardown_function():
    reset_memory_mcp_registry()
    main_module.app.dependency_overrides.clear()


def payload(**overrides):
    return {
        "name": "Finance MCP",
        "transport": "streamable-http",
        "endpoint": "https://mcp.example.com/mcp",
        "auth_env_ref": "FINANCE_MCP_TOKEN",
        "allowed_tools": ["schema.list", "report.read"],
        "enabled": True,
        "read_only_only": True,
        "allow_private_network": False,
        "request_timeout_seconds": 2.0,
        "max_concurrency": 2,
        "daily_call_limit": 10,
        "circuit_failure_threshold": 2,
        "circuit_reset_seconds": 30,
        **overrides,
    }


class ApiTransport:
    async def discover(self, _server, _token):
        return [McpToolDescriptor(name="report.read", read_only=True, destructive=False, idempotent=True)]

    async def execute(self, _server, _token, _tool_name, _arguments, validate_tools):
        tools = await self.discover(_server, _token)
        validate_tools(tools)
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}


def install_invocation_service(monkeypatch):
    service = McpInvocationService(
        mcp_routes.get_mcp_registry_service(),
        transport=ApiTransport(),
        guard=McpRuntimeGuard(backend="memory", queue_timeout_seconds=0.01),
        resolver=lambda _hostname: ["8.8.8.8"],
        environ={"FINANCE_MCP_TOKEN": "api-secret"},
    )
    monkeypatch.setattr(mcp_routes, "get_mcp_invocation_service", lambda: service)


@pytest.mark.parametrize("role", ["viewer", "analyst"])
def test_mcp_registry_requires_manage_permission(role):
    identity({"subject": role, "tenant_id": "tenant-a", "roles": [role]})
    assert client.get("/api/v1/mcp-servers").status_code == 403
    assert client.post("/api/v1/mcp-servers", json=payload()).status_code == 403
    assert client.post("/api/v1/mcp-servers/missing/health").status_code == 403
    assert client.post("/api/v1/mcp-servers/missing/invoke", json={"tool_name": "report.read", "arguments": {}}).status_code == 403


def test_mcp_registry_api_never_accepts_or_returns_inline_secret(monkeypatch):
    monkeypatch.setenv("FINANCE_MCP_TOKEN", "do-not-return-this-secret")
    identity({"subject": "1", "tenant_id": "tenant-a", "roles": ["admin"]})

    rejected = client.post(
        "/api/v1/mcp-servers",
        json={**payload(), "bearer_token": "inline-secret"},
    )
    assert rejected.status_code == 422

    created = client.post("/api/v1/mcp-servers", json=payload())
    assert created.status_code == 200
    assert created.json()["credential_configured"] is True
    assert created.json()["auth_env_ref"] == "FINANCE_MCP_TOKEN"
    assert "do-not-return-this-secret" not in created.text
    assert "do-not-return-this-secret" not in client.get("/api/v1/mcp-servers").text


def test_mcp_registry_api_isolates_patch_and_delete_by_tenant():
    identity({"subject": "1", "tenant_id": "tenant-a", "roles": ["admin"]})
    created = client.post("/api/v1/mcp-servers", json=payload()).json()

    identity({"subject": "2", "tenant_id": "tenant-b", "roles": ["admin"]})
    assert client.get("/api/v1/mcp-servers").json() == []
    assert client.patch(f"/api/v1/mcp-servers/{created['id']}", json={"enabled": False}).status_code == 404
    assert client.delete(f"/api/v1/mcp-servers/{created['id']}").status_code == 404

    identity({"subject": "1", "tenant_id": "tenant-a", "roles": ["admin"]})
    updated = client.patch(
        f"/api/v1/mcp-servers/{created['id']}",
        json={"enabled": False, "allowed_tools": ["report.read"]},
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    assert updated.json()["allowed_tools"] == ["report.read"]
    assert client.delete(f"/api/v1/mcp-servers/{created['id']}").status_code == 204
    assert client.get("/api/v1/mcp-servers").json() == []


def test_mcp_registry_api_rejects_unsafe_endpoint_and_wildcard():
    identity({"subject": "1", "tenant_id": "tenant-a", "roles": ["admin"]})
    assert client.post(
        "/api/v1/mcp-servers",
        json=payload(endpoint="http://mcp.example.com/mcp"),
    ).status_code == 422


def test_mcp_health_and_invoke_api_are_admin_tenant_scoped(monkeypatch):
    install_invocation_service(monkeypatch)
    identity({"subject": "1", "tenant_id": "tenant-a", "roles": ["admin"]})
    created = client.post("/api/v1/mcp-servers", json=payload(allowed_tools=["report.read"])).json()
    health = client.post(f"/api/v1/mcp-servers/{created['id']}/health")
    assert health.status_code == 200
    assert health.json()["health_status"] == "healthy"
    invoked = client.post(
        f"/api/v1/mcp-servers/{created['id']}/invoke",
        json={"tool_name": "report.read", "arguments": {"region": "华东"}},
    )
    assert invoked.status_code == 200
    assert invoked.json()["result"]["isError"] is False

    identity({"subject": "2", "tenant_id": "tenant-b", "roles": ["admin"]})
    assert client.post(f"/api/v1/mcp-servers/{created['id']}/health").status_code == 404
    assert client.post(
        f"/api/v1/mcp-servers/{created['id']}/invoke",
        json={"tool_name": "report.read", "arguments": {}},
    ).status_code == 404

    assert client.post(
        "/api/v1/mcp-servers",
        json=payload(name="Wildcard", allowed_tools=["*"]),
    ).status_code == 422

