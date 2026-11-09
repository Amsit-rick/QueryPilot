from contextlib import contextmanager
from datetime import datetime, timezone

from app.domain.mcp_registry import McpServerRegistration
from app.repositories import mysql_mcp_registry_repository as repository_module
from app.repositories.mysql_mcp_registry_repository import MySQLMcpRegistryRepository


class FakeCursor:
    def __init__(self, rows=(), rowcount=0):
        self.rows = list(rows)
        self.rowcount = rowcount
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return tuple(self.rows)


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True


def fake_connect(connection):
    @contextmanager
    def _connect():
        yield connection

    return _connect


def registration():
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    return McpServerRegistration(
        id="server-1",
        tenant_id="tenant-a",
        name="Finance MCP",
        transport="streamable-http",
        endpoint="https://mcp.example.com/mcp",
        auth_env_ref="FINANCE_MCP_TOKEN",
        allowed_tools=("schema.list", "report.read"),
        enabled=True,
        read_only_only=True,
        created_at=now,
        updated_at=now,
    )


def test_mysql_repository_serializes_allowlist_without_secret_value(monkeypatch):
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    monkeypatch.setattr(repository_module, "connect", fake_connect(connection))

    server = registration()
    assert MySQLMcpRegistryRepository().create(server) == server
    sql, params = cursor.calls[0]
    assert "INSERT INTO mcp_server_registry" in sql
    assert params[1] == "tenant-a"
    assert params[5] == "FINANCE_MCP_TOKEN"
    assert params[6] == '["schema.list", "report.read"]'
    assert "secret-value" not in str(params)
    assert connection.committed is True


def test_mysql_repository_reads_and_deletes_with_tenant_scope(monkeypatch):
    server = registration()
    row = (
        server.id,
        server.tenant_id,
        server.name,
        server.transport,
        server.endpoint,
        server.auth_env_ref,
        '["schema.list", "report.read"]',
        1,
        1,
        0,
        10.0,
        4,
        1000,
        3,
        30,
        "unchecked",
        0,
        None,
        "[]",
        None,
        server.created_at,
        server.updated_at,
    )
    read_cursor = FakeCursor(rows=[row])
    monkeypatch.setattr(repository_module, "connect", fake_connect(FakeConnection(read_cursor)))
    loaded = MySQLMcpRegistryRepository().get("server-1", "tenant-a")
    assert loaded == server
    assert read_cursor.calls[0][1] == ("server-1", "tenant-a")

    delete_cursor = FakeCursor(rowcount=1)
    delete_connection = FakeConnection(delete_cursor)
    monkeypatch.setattr(repository_module, "connect", fake_connect(delete_connection))
    assert MySQLMcpRegistryRepository().delete("server-1", "tenant-a") is True
    assert delete_cursor.calls[0][1] == ("server-1", "tenant-a")
    assert delete_connection.committed is True

