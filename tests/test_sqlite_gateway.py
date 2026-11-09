from pathlib import Path

import pytest

from app.domain.data_gateway import GatewayError, GatewayQuery
from app.gateways.sqlite_gateway import SQLiteGateway


def _gateway(tmp_path: Path):
    database = tmp_path / "demo.db"
    database.write_bytes(b"sqlite")
    calls = []
    gateway = SQLiteGateway(
        gateway_id="sqlite-demo",
        name="SQLite Demo",
        database_path=str(database),
        schema_loader=lambda path: calls.append(("schema", path)) or {"sales": {"amount", "id"}},
        executor=lambda path, sql, max_rows, params, timeout, offset: calls.append(("query", path, sql, max_rows, params, timeout, offset)) or (["id"], [[1]], False),
        timeout_seconds=7,
    )
    return gateway, calls


def test_sqlite_gateway_exposes_contract_and_executes_query(tmp_path):
    gateway, calls = _gateway(tmp_path)

    assert gateway.descriptor.backend == "sqlite"
    assert gateway.descriptor.dialect == "sqlite"
    assert gateway.capabilities.cancel is False
    assert gateway.schema() == {"sales": ["amount", "id"]}
    result = gateway.query(GatewayQuery(sql="SELECT id FROM sales", params={"x": 1}, max_rows=10, offset=2))
    assert result.rows == [[1]] and result.operation_id
    assert calls[-1][3:] == (10, {"x": 1}, 7, 2)


def test_sqlite_gateway_preview_validates_table_against_schema(tmp_path):
    gateway, calls = _gateway(tmp_path)
    result = gateway.preview("sales", limit=5, offset=1)
    assert result.columns == ["id"]
    assert calls[-1][2] == 'SELECT * FROM "sales" LIMIT 7'
    with pytest.raises(GatewayError) as exc:
        gateway.preview("sales; DROP TABLE sales")
    assert exc.value.code == "E_GATEWAY_TABLE"


def test_sqlite_gateway_health_does_not_expose_database_path(tmp_path):
    gateway, _ = _gateway(tmp_path)
    assert gateway.test_connection().status == "healthy"
    missing = SQLiteGateway(gateway_id="missing", name="Missing", database_path=str(tmp_path / "secret" / "missing.db"), schema_loader=lambda _path: {}, executor=lambda *_args: ([], [], False))
    health = missing.health()
    assert health.status == "unavailable"
    assert str(tmp_path) not in health.message
