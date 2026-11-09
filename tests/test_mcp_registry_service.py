import json

import pytest

from app.config import settings
from app.repositories.memory_mcp_registry_repository import InMemoryMcpRegistryRepository
from app.services.mcp_registry_service import McpRegistryService


def server_payload(**overrides):
    return {
        "name": "Finance MCP",
        "transport": "streamable-http",
        "endpoint": "https://mcp.example.com/mcp",
        "auth_env_ref": "FINANCE_MCP_TOKEN",
        "allowed_tools": ["schema.list", "report.read"],
        "enabled": True,
        "read_only_only": True,
        **overrides,
    }


def test_registry_crud_is_tenant_scoped_and_audited(tmp_path, monkeypatch):
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(settings, "audit_log_path", str(audit_path))
    repository = InMemoryMcpRegistryRepository()
    service = McpRegistryService(repository)

    created = service.create_server(tenant_id="tenant-a", actor_id="admin-a", **server_payload())
    assert created.allowed_tools == ("schema.list", "report.read")
    assert service.list_servers("tenant-a") == [created]
    assert service.list_servers("tenant-b") == []
    assert service.get_server(created.id, "tenant-b") is None

    changed = service.update_server(
        created.id,
        "tenant-a",
        {"enabled": False, "allowed_tools": ["report.read", "report.read"]},
        actor_id="admin-a",
    )
    assert changed.enabled is False
    assert changed.allowed_tools == ("report.read",)

    with pytest.raises(LookupError):
        service.update_server(created.id, "tenant-b", {"enabled": True}, actor_id="admin-b")
    with pytest.raises(LookupError):
        service.delete_server(created.id, "tenant-b", actor_id="admin-b")

    service.delete_server(created.id, "tenant-a", actor_id="admin-a")
    assert service.get_server(created.id, "tenant-a") is None

    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [record["event"] for record in records] == [
        "mcp_server_created",
        "mcp_server_updated",
        "mcp_server_deleted",
    ]
    assert all(record["tenant_id"] == "tenant-a" for record in records)
    assert "FINANCE_MCP_TOKEN" not in audit_path.read_text(encoding="utf-8")


def test_registry_endpoint_and_allowlist_validation():
    service = McpRegistryService(InMemoryMcpRegistryRepository())

    loopback = service.create_server(
        tenant_id="tenant-a",
        actor_id="admin",
        **server_payload(name="Local MCP", endpoint="http://127.0.0.1:9000/mcp"),
    )
    assert loopback.endpoint == "http://127.0.0.1:9000/mcp"

    invalid = (
        (server_payload(name="Remote HTTP", endpoint="http://mcp.example.com/mcp"), "E_MCP_ENDPOINT_TLS"),
        (server_payload(name="URL secret", endpoint="https://user:secret@mcp.example.com/mcp"), "E_MCP_ENDPOINT"),
        (server_payload(name="URL query", endpoint="https://mcp.example.com/mcp?token=x"), "E_MCP_ENDPOINT"),
        (server_payload(name="Wildcard", allowed_tools=["*"]), "E_MCP_TOOL_ALLOWLIST"),
        (server_payload(name="Empty tools", allowed_tools=[]), "E_MCP_TOOL_ALLOWLIST"),
        (server_payload(name="Writable", read_only_only=False), "E_MCP_READ_ONLY"),
        (server_payload(name="Stdio", transport="stdio"), "E_MCP_TRANSPORT"),
        (server_payload(name="Bad secret ref", auth_env_ref="inline-secret"), "E_MCP_SECRET_REF"),
    )
    for payload, code in invalid:
        with pytest.raises(ValueError, match=code):
            service.create_server(tenant_id="tenant-a", actor_id="admin", **payload)


def test_registry_rejects_duplicate_names_per_tenant_only():
    service = McpRegistryService(InMemoryMcpRegistryRepository())
    service.create_server(tenant_id="tenant-a", actor_id="admin", **server_payload())
    with pytest.raises(ValueError, match="E_MCP_DUPLICATE"):
        service.create_server(
            tenant_id="tenant-a",
            actor_id="admin",
            **server_payload(name=" finance mcp "),
        )
    service.create_server(tenant_id="tenant-b", actor_id="admin", **server_payload())

