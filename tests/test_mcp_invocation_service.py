import asyncio
import json

import pytest

from app.config import settings
from app.repositories.memory_mcp_registry_repository import InMemoryMcpRegistryRepository
from app.services.mcp_invocation_service import (
    McpInvocationError,
    McpInvocationService,
    McpToolDescriptor,
)
from app.services.mcp_registry_service import McpRegistryService
from app.services.mcp_runtime_guard import McpRuntimeGuard


def run(coro):
    return asyncio.run(coro)


def server_payload(**overrides):
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


class FakeTransport:
    def __init__(self, tools, result=None, failure=None):
        self.tools = list(tools)
        self.result = result or {"content": [{"type": "text", "text": "ok"}], "isError": False}
        self.failure = failure
        self.discover_calls = []
        self.execute_calls = []

    async def discover(self, server, token):
        self.discover_calls.append((server.id, token))
        if self.failure:
            raise self.failure
        return list(self.tools)

    async def execute(self, server, token, tool_name, arguments, validate_tools):
        self.execute_calls.append((server.id, token, tool_name, arguments))
        if self.failure:
            raise self.failure
        validate_tools(self.tools)
        return self.result


def build_service(tmp_path, monkeypatch, *, tools, result=None, failure=None, resolver=None, environ=None):
    monkeypatch.setattr(settings, "audit_log_path", str(tmp_path / "audit.jsonl"))
    repository = InMemoryMcpRegistryRepository()
    registry = McpRegistryService(repository)
    transport = FakeTransport(tools, result=result, failure=failure)
    service = McpInvocationService(
        registry,
        transport=transport,
        guard=McpRuntimeGuard(backend="memory", queue_timeout_seconds=0.01),
        resolver=resolver or (lambda _hostname: ["8.8.8.8"]),
        environ=environ if environ is not None else {"FINANCE_MCP_TOKEN": "secret-token"},
    )
    return registry, service, transport


def readonly_tool(name="report.read"):
    return McpToolDescriptor(
        name=name,
        read_only=True,
        destructive=False,
        idempotent=True,
    )


def test_health_and_invoke_use_allowlist_readonly_annotation_and_sanitized_audit(tmp_path, monkeypatch):
    registry, service, transport = build_service(
        tmp_path,
        monkeypatch,
        tools=[readonly_tool("schema.list"), readonly_tool("report.read")],
    )
    server = registry.create_server(tenant_id="tenant-a", actor_id="admin", **server_payload())

    health = run(service.check_health(server.id, "tenant-a", actor_id="admin"))
    assert health.health_status == "healthy"
    assert health.discovered_tools == ("schema.list", "report.read")
    result = run(
        service.invoke_tool(
            server.id,
            "tenant-a",
            actor_id="admin",
            tool_name="report.read",
            arguments={"region": "华东"},
        )
    )
    assert result["tool_name"] == "report.read"
    assert result["result"]["isError"] is False
    assert result["replay_policy"]["replay_class"] == "safe"
    assert result["replay_policy"]["idempotent"] is True
    assert transport.execute_calls[0][2:] == ("report.read", {"region": "华东"})

    audit_text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    records = [json.loads(line) for line in audit_text.splitlines()]
    assert {record["event"] for record in records} >= {"mcp_health_checked", "mcp_tool_called"}
    called = next(record for record in records if record["event"] == "mcp_tool_called")
    assert called["tool_replay_class"] == "safe"
    assert called["tool_replay_policy_version"] == "tool-replay-v1"
    assert "secret-token" not in audit_text
    assert "华东" not in audit_text


def test_readonly_but_non_idempotent_mcp_tool_is_never_marked_safe(tmp_path, monkeypatch):
    descriptor = McpToolDescriptor(
        name="report.read",
        read_only=True,
        destructive=False,
        idempotent=False,
    )
    registry, service, _transport = build_service(tmp_path, monkeypatch, tools=[descriptor])
    server = registry.create_server(tenant_id="tenant-a", actor_id="admin", **server_payload())

    result = run(
        service.invoke_tool(
            server.id,
            "tenant-a",
            actor_id="admin",
            tool_name="report.read",
            arguments={},
        )
    )

    assert result["replay_policy"]["replay_class"] == "unsafe"
    assert result["replay_policy"]["idempotent"] is False


def test_transport_must_supply_validated_tool_descriptor_before_result_is_accepted(tmp_path, monkeypatch):
    registry, service, transport = build_service(
        tmp_path,
        monkeypatch,
        tools=[readonly_tool()],
    )
    server = registry.create_server(tenant_id="tenant-a", actor_id="admin", **server_payload())

    async def execute_without_catalog(_server, _token, _tool_name, _arguments, _validate_tools):
        return transport.result

    transport.execute = execute_without_catalog

    with pytest.raises(McpInvocationError, match="E_MCP_TOOL_CATALOG_INVALID"):
        run(
            service.invoke_tool(
                server.id,
                "tenant-a",
                actor_id="admin",
                tool_name="report.read",
                arguments={},
            )
        )


def test_invocation_rejects_missing_secret_disabled_and_non_allowlisted_without_network(tmp_path, monkeypatch):
    registry, service, transport = build_service(
        tmp_path,
        monkeypatch,
        tools=[readonly_tool()],
        environ={},
    )
    server = registry.create_server(tenant_id="tenant-a", actor_id="admin", **server_payload())
    with pytest.raises(McpInvocationError, match="E_MCP_SECRET_MISSING"):
        run(service.invoke_tool(server.id, "tenant-a", actor_id="admin", tool_name="report.read", arguments={}))
    assert transport.execute_calls == []

    service._environ["FINANCE_MCP_TOKEN"] = "configured"
    with pytest.raises(McpInvocationError, match="E_MCP_TOOL_FORBIDDEN"):
        run(service.invoke_tool(server.id, "tenant-a", actor_id="admin", tool_name="admin.delete", arguments={}))
    assert transport.execute_calls == []

    registry.update_server(server.id, "tenant-a", {"enabled": False}, actor_id="admin")
    with pytest.raises(McpInvocationError, match="E_MCP_DISABLED"):
        run(service.invoke_tool(server.id, "tenant-a", actor_id="admin", tool_name="report.read", arguments={}))
    assert transport.execute_calls == []


@pytest.mark.parametrize(
    "descriptor",
    [
        McpToolDescriptor(name="report.read", read_only=None, destructive=False, idempotent=True),
        McpToolDescriptor(name="report.read", read_only=False, destructive=False, idempotent=True),
        McpToolDescriptor(name="report.read", read_only=True, destructive=True, idempotent=True),
    ],
)
def test_invocation_requires_explicit_readonly_non_destructive_tool(tmp_path, monkeypatch, descriptor):
    registry, service, transport = build_service(tmp_path, monkeypatch, tools=[descriptor])
    server = registry.create_server(tenant_id="tenant-a", actor_id="admin", **server_payload())
    with pytest.raises(McpInvocationError, match="E_MCP_TOOL_NOT_READ_ONLY"):
        run(service.invoke_tool(server.id, "tenant-a", actor_id="admin", tool_name="report.read", arguments={}))
    assert len(transport.execute_calls) == 1


def test_invocation_rejects_duplicate_remote_tool_names(tmp_path, monkeypatch):
    registry, service, _transport = build_service(
        tmp_path,
        monkeypatch,
        tools=[readonly_tool(), readonly_tool()],
    )
    server = registry.create_server(tenant_id="tenant-a", actor_id="admin", **server_payload())
    with pytest.raises(McpInvocationError, match="E_MCP_TOOL_CATALOG_INVALID"):
        run(service.invoke_tool(server.id, "tenant-a", actor_id="admin", tool_name="report.read", arguments={}))


def test_target_policy_blocks_private_network_by_default_and_requires_explicit_override(tmp_path, monkeypatch):
    registry, service, transport = build_service(
        tmp_path,
        monkeypatch,
        tools=[readonly_tool("schema.list"), readonly_tool("report.read")],
        resolver=lambda _hostname: ["10.20.30.40"],
    )
    server = registry.create_server(tenant_id="tenant-a", actor_id="admin", **server_payload())
    with pytest.raises(McpInvocationError, match="E_MCP_TARGET_PRIVATE"):
        run(service.check_health(server.id, "tenant-a", actor_id="admin"))
    assert transport.discover_calls == []

    registry.update_server(server.id, "tenant-a", {"allow_private_network": True}, actor_id="admin")
    checked = run(service.check_health(server.id, "tenant-a", actor_id="admin"))
    assert checked.health_status == "healthy"
    assert len(transport.discover_calls) == 1


@pytest.mark.parametrize("target", ["169.254.169.254", "100.100.100.200", "168.63.129.16", "fd00:ec2::254"])
def test_link_local_and_cloud_infrastructure_targets_are_always_blocked(tmp_path, monkeypatch, target):
    registry, service, transport = build_service(
        tmp_path,
        monkeypatch,
        tools=[readonly_tool()],
        resolver=lambda _hostname: [target],
    )
    server = registry.create_server(
        tenant_id="tenant-a",
        actor_id="admin",
        **server_payload(allow_private_network=True),
    )
    with pytest.raises(McpInvocationError, match="E_MCP_TARGET_FORBIDDEN"):
        run(service.check_health(server.id, "tenant-a", actor_id="admin"))
    assert transport.discover_calls == []


def test_argument_and_result_byte_budgets_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "mcp_max_arguments_bytes", 24)
    monkeypatch.setattr(settings, "mcp_max_result_bytes", 40)
    registry, service, transport = build_service(
        tmp_path,
        monkeypatch,
        tools=[readonly_tool()],
        result={"content": [{"type": "text", "text": "x" * 100}], "isError": False},
    )
    server = registry.create_server(tenant_id="tenant-a", actor_id="admin", **server_payload())
    with pytest.raises(McpInvocationError, match="E_MCP_ARGUMENTS_TOO_LARGE"):
        run(service.invoke_tool(server.id, "tenant-a", actor_id="admin", tool_name="report.read", arguments={"value": "x" * 50}))
    assert transport.execute_calls == []

    with pytest.raises(McpInvocationError, match="E_MCP_RESULT_TOO_LARGE"):
        run(service.invoke_tool(server.id, "tenant-a", actor_id="admin", tool_name="report.read", arguments={}))
    status = registry.get_server(server.id, "tenant-a")
    assert status.health_status == "unhealthy"
    assert status.last_error_code == "E_MCP_RESULT_TOO_LARGE"


@pytest.mark.parametrize(
    ("result", "code", "expected_status", "event"),
    [
        ({"content": [], "isError": False, "value": float("nan")}, "E_MCP_RESULT_INVALID", "unhealthy", "mcp_tool_failed"),
        ({"content": [], "isError": False, "resultType": "input_required"}, "E_MCP_INTERACTION_REQUIRED", "policy_blocked", "mcp_policy_blocked"),
    ],
)
def test_invalid_or_interactive_results_update_status_and_audit(
    tmp_path,
    monkeypatch,
    result,
    code,
    expected_status,
    event,
):
    registry, service, _transport = build_service(
        tmp_path,
        monkeypatch,
        tools=[readonly_tool()],
        result=result,
    )
    server = registry.create_server(tenant_id="tenant-a", actor_id="admin", **server_payload())

    with pytest.raises(McpInvocationError, match=code):
        run(service.invoke_tool(server.id, "tenant-a", actor_id="admin", tool_name="report.read", arguments={}))

    status = registry.get_server(server.id, "tenant-a")
    assert status.health_status == expected_status
    assert status.last_error_code == code
    records = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(record["event"] == event and record["error_code"] == code for record in records)


def test_transport_failures_are_sanitized_and_open_circuit(tmp_path, monkeypatch):
    registry, service, _transport = build_service(
        tmp_path,
        monkeypatch,
        tools=[readonly_tool()],
        failure=TimeoutError("request timed out with token=secret-token"),
    )
    server = registry.create_server(
        tenant_id="tenant-a",
        actor_id="admin",
        **server_payload(circuit_failure_threshold=1),
    )
    with pytest.raises(McpInvocationError, match="E_MCP_TIMEOUT") as first:
        run(service.invoke_tool(server.id, "tenant-a", actor_id="admin", tool_name="report.read", arguments={}))
    assert "secret-token" not in str(first.value)
    with pytest.raises(McpInvocationError, match="E_MCP_CIRCUIT_OPEN"):
        run(service.invoke_tool(server.id, "tenant-a", actor_id="admin", tool_name="report.read", arguments={}))

