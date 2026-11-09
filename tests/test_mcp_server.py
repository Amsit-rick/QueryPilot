import json
import subprocess
import sys
from pathlib import Path

import anyio

from app.mcp_server import create_mcp_server


def test_mcp_server_exposes_readonly_tools_and_skill_catalog():
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    server = create_mcp_server(
        identity_provider=lambda _context: {
            "subject": "mcp-test",
            "tenant_id": "default",
            "roles": ["analyst"],
        }
    )

    async def exercise():
        async with InMemoryTransport(server) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                tools = await session.list_tools()
                resource_templates = await session.list_resource_templates()

                assert {tool.name for tool in tools.tools} == {
                    "schema.list",
                    "query.plan",
                    "task.status",
                    "result.get",
                    "skill.route",
                }
                assert "query.execute" not in {tool.name for tool in tools.tools}
                assert all(tool.annotations.read_only_hint is True for tool in tools.tools)
                assert all(tool.annotations.destructive_hint is False for tool in tools.tools)
                assert all("ctx" not in tool.input_schema.get("properties", {}) for tool in tools.tools)
                assert {template.uri_template for template in resource_templates.resource_templates} == {
                    "querypilot://skills/catalog/{tenant_id}"
                }

                catalog = await session.read_resource("querypilot://skills/catalog/default")
                catalog_payload = json.loads(catalog.contents[0].text)
                assert len(catalog_payload["skills"]) == 5

                routed = await session.call_tool(
                    "skill.route",
                    {"question": "统计待回款金额"},
                )
                payload = routed.structured_content or json.loads(routed.content[0].text)
                assert payload["selected_skill"]["id"] == "receivables-analysis"

    anyio.run(exercise)


def test_mcp_skill_catalog_uses_same_tenant_canary_resolution(monkeypatch):
    from app.config import settings
    from app.domain.skill import SkillDefinition
    from app.skill_runtime import get_skill_service, reset_memory_skill_repository
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    monkeypatch.setattr(settings, "database_backend", "sqlite")
    reset_memory_skill_repository()
    service = get_skill_service()
    version = service.create_version(
        tenant_id="tenant-canary",
        actor_id="admin-1",
        definition=SkillDefinition(
            id="sales-analysis",
            version="1.1.0",
            name="销售分析",
            description="MCP 灰度版本",
            intents=("sales_query",),
            trigger_terms=("销售",),
            allowed_tools=("schema.list", "query.plan"),
            workflow_steps=("确认口径",),
        ),
    )
    service.start_canary(
        version.id,
        tenant_id="tenant-canary",
        actor_id="admin-1",
        percentage=100,
    )
    server = create_mcp_server(
        identity_provider=lambda _context: {
            "subject": "viewer-7",
            "tenant_id": "tenant-canary",
            "roles": ["viewer"],
        }
    )

    async def exercise():
        async with InMemoryTransport(server) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                catalog = await session.read_resource(
                    "querypilot://skills/catalog/tenant-canary"
                )
                payload = json.loads(catalog.contents[0].text)
                selected = next(
                    item for item in payload["skills"] if item["id"] == "sales-analysis"
                )
                assert selected["version"] == "1.1.0"
                assert selected["deployment"]["state"] == "canary"

    try:
        anyio.run(exercise)
    finally:
        reset_memory_skill_repository()


def test_mcp_server_requires_explicit_identity_when_auth_is_enabled(monkeypatch):
    import pytest

    from app.config import settings
    from app.mcp_server import resolve_mcp_identity

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.delenv("QUERY_PILOT_MCP_TOKEN", raising=False)

    with pytest.raises(PermissionError, match="MCP 身份凭证"):
        resolve_mcp_identity()


def test_mcp_stdio_accepts_existing_api_key_identity(monkeypatch):
    from app.config import settings
    from app.mcp_server import resolve_mcp_identity

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "api_keys", "mcp-test-key")
    monkeypatch.setenv("QUERY_PILOT_MCP_TOKEN", "mcp-test-key")

    identity = resolve_mcp_identity()

    assert identity["subject"].startswith("api-key:")
    assert identity["roles"] == ["analyst"]


def test_mcp_http_never_falls_back_to_process_token(monkeypatch):
    import pytest

    from app.config import settings
    from app.mcp_server import resolve_mcp_identity

    class HttpContext:
        headers = {}

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "api_keys", "shared-process-token")
    monkeypatch.setenv("QUERY_PILOT_MCP_TOKEN", "shared-process-token")

    with pytest.raises(PermissionError, match="MCP 身份凭证"):
        resolve_mcp_identity(HttpContext())


def test_mcp_server_script_is_directly_executable():
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "scripts" / "run_mcp_server.py"),
            "--help",
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert "streamable-http" in result.stdout


def test_mcp_server_refuses_remote_bind_without_explicit_override():
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "scripts" / "run_mcp_server.py"),
            "--transport",
            "streamable-http",
            "--host",
            "0.0.0.0",
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode != 0
    assert "requires --allow-remote" in result.stderr
