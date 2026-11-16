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


def test_registry_validates_oauth_client_credentials_contract():
    service = McpRegistryService(InMemoryMcpRegistryRepository())
    created = service.create_server(
        tenant_id="tenant-a",
        actor_id="admin",
        **server_payload(
            auth_type="oauth_client_credentials",
            auth_env_ref="env://FINANCE_MCP_CLIENT_SECRET",
            oauth_token_url="https://identity.example.com/oauth/token",
            oauth_client_id="query-pilot",
            oauth_scopes=["mcp.read", "catalog.read", "mcp.read"],
        ),
    )

    assert created.auth_type == "oauth_client_credentials"
    assert created.oauth_scopes == ("mcp.read", "catalog.read")
    assert created.credential_version == 1

    invalid = (
        server_payload(name="OAuth missing URL", auth_type="oauth_client_credentials"),
        server_payload(name="OAuth HTTP", auth_type="oauth_client_credentials", oauth_token_url="http://identity.example.com/token", oauth_client_id="client"),
        server_payload(name="Bearer with OAuth", oauth_token_url="https://identity.example.com/token", oauth_client_id="client"),
        server_payload(name="Bad scope", auth_type="oauth_client_credentials", oauth_token_url="https://identity.example.com/token", oauth_client_id="client", oauth_scopes=["bad scope!"]),
    )
    for candidate in invalid:
        with pytest.raises(ValueError, match="E_MCP_OAUTH"):
            service.create_server(tenant_id="tenant-a", actor_id="admin", **candidate)


def test_credential_rotation_increments_version_resets_health_and_is_tenant_scoped(tmp_path, monkeypatch):
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(settings, "audit_log_path", str(audit_path))
    service = McpRegistryService(InMemoryMcpRegistryRepository())
    created = service.create_server(tenant_id="tenant-a", actor_id="admin", **server_payload())
    checked = service.record_runtime_status(created.id, "tenant-a", status="healthy", discovered_tools=("report.read",))

    rotated = service.rotate_credentials(
        checked.id,
        "tenant-a",
        {"auth_env_ref": "env://FINANCE_MCP_TOKEN_V2"},
        actor_id="admin",
    )

    assert rotated.auth_env_ref == "env://FINANCE_MCP_TOKEN_V2"
    assert rotated.credential_version == 2
    assert rotated.credential_rotated_at is not None
    assert rotated.health_status == "unchecked"
    assert rotated.discovered_tools == ()
    with pytest.raises(LookupError):
        service.rotate_credentials(created.id, "tenant-b", {"auth_env_ref": "env://OTHER"}, actor_id="admin-b")
    audit_text = audit_path.read_text(encoding="utf-8")
    assert "mcp_credentials_rotated" in audit_text
    assert "FINANCE_MCP_TOKEN_V2" not in audit_text


def test_regular_update_cannot_bypass_credential_rotation_versioning():
    service = McpRegistryService(InMemoryMcpRegistryRepository())
    created = service.create_server(tenant_id="tenant-a", actor_id="admin", **server_payload())

    with pytest.raises(ValueError, match="E_MCP_CREDENTIAL_ROTATION_REQUIRED"):
        service.update_server(
            created.id,
            "tenant-a",
            {"auth_env_ref": "env://FINANCE_MCP_TOKEN_V2"},
            actor_id="admin",
        )

    current = service.get_server(created.id, "tenant-a")
    assert current.auth_env_ref == "FINANCE_MCP_TOKEN"
    assert current.credential_version == 1

