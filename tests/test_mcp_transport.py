import asyncio
from contextlib import asynccontextmanager

from mcp import types

from app.config import settings
from app.services.mcp_transport import OfficialMcpTransport


def run(coro):
    return asyncio.run(coro)


def tool(name: str, *, read_only=True, destructive=False, idempotent=True):
    return types.Tool(
        name=name,
        inputSchema={"type": "object"},
        annotations=types.ToolAnnotations(
            readOnlyHint=read_only,
            destructiveHint=destructive,
            idempotentHint=idempotent,
        ),
    )


class FakeSession:
    def __init__(self, pages, result=None):
        self.pages = list(pages)
        self.result = result
        self.list_cursors = []
        self.calls = []
        self.validated = False

    async def list_tools(self, params=None):
        self.list_cursors.append(None if params is None else params.cursor)
        return self.pages.pop(0)

    async def call_tool(self, name, *, arguments, read_timeout_seconds, allow_input_required):
        assert self.validated is True
        self.calls.append(
            {
                "name": name,
                "arguments": arguments,
                "read_timeout_seconds": read_timeout_seconds,
                "allow_input_required": allow_input_required,
            }
        )
        return self.result


class FakeTransport(OfficialMcpTransport):
    def __init__(self, session):
        self.session = session
        self.session_count = 0

    @asynccontextmanager
    async def _session(self, server, token: str):
        del server, token
        self.session_count += 1
        yield self.session


class Server:
    request_timeout_seconds = 7.5


def test_discover_paginates_and_maps_tool_annotations(monkeypatch):
    monkeypatch.setattr(settings, "mcp_max_tool_pages", 3)
    monkeypatch.setattr(settings, "mcp_max_discovered_tools", 10)
    session = FakeSession(
        [
            types.ListToolsResult(tools=[tool("report.read")], nextCursor="page-2"),
            types.ListToolsResult(
                tools=[tool("schema.list", read_only=True, destructive=None, idempotent=None)]
            ),
        ]
    )
    transport = FakeTransport(session)

    discovered = run(transport.discover(Server(), "token"))

    assert session.list_cursors == [None, "page-2"]
    assert [item.name for item in discovered] == ["report.read", "schema.list"]
    assert discovered[0].read_only is True
    assert discovered[0].destructive is False
    assert discovered[0].idempotent is True
    assert discovered[1].destructive is None


def test_execute_validates_catalog_and_calls_tool_in_same_session(monkeypatch):
    monkeypatch.setattr(settings, "mcp_max_tool_pages", 2)
    monkeypatch.setattr(settings, "mcp_max_discovered_tools", 10)
    result = types.CallToolResult(
        content=[types.TextContent(text="ok")],
        structuredContent={"rows": 1},
        isError=False,
        resultType="complete",
    )
    session = FakeSession([types.ListToolsResult(tools=[tool("report.read")])], result=result)
    transport = FakeTransport(session)

    def validate_tools(tools):
        assert [item.name for item in tools] == ["report.read"]
        session.validated = True

    payload = run(
        transport.execute(
            Server(),
            "token",
            "report.read",
            {"region": "华东"},
            validate_tools,
        )
    )

    assert transport.session_count == 1
    assert session.calls == [
        {
            "name": "report.read",
            "arguments": {"region": "华东"},
            "read_timeout_seconds": 7.5,
            "allow_input_required": False,
        }
    ]
    assert payload["structuredContent"] == {"rows": 1}
    assert payload["isError"] is False
    assert payload["resultType"] == "complete"
    assert "structured_content" not in payload


def test_discover_rejects_catalog_beyond_configured_limit(monkeypatch):
    monkeypatch.setattr(settings, "mcp_max_tool_pages", 2)
    monkeypatch.setattr(settings, "mcp_max_discovered_tools", 1)
    session = FakeSession(
        [types.ListToolsResult(tools=[tool("one"), tool("two")])]
    )

    try:
        run(FakeTransport(session).discover(Server(), "token"))
    except RuntimeError as exc:
        assert "catalog exceeds configured limit" in str(exc)
    else:
        raise AssertionError("expected oversized catalog rejection")
