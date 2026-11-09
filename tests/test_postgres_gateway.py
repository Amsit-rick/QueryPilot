import os
from decimal import Decimal

import pytest

from app.domain.data_gateway import GatewayError, GatewayQuery
from app.gateways.postgres_gateway import PostgreSQLGateway


class _Cursor:
    def __enter__(self): return self
    def __exit__(self, *_args): return None
    def execute(self, sql):
        if sql != "SELECT 1": raise AssertionError(sql)


class _Connection:
    def __enter__(self): return self
    def __exit__(self, *_args): return None
    def cursor(self): return _Cursor()


def _gateway():
    calls = []
    gateway = PostgreSQLGateway(
        gateway_id="postgres-main",
        name="PostgreSQL Main",
        schema_loader=lambda: {"sales": {"id", "amount"}},
        executor=lambda sql, params, max_rows, offset: calls.append((sql, params, max_rows, offset)) or (["amount"], [[Decimal("10.50")]], False),
        connect=lambda: _Connection(),
    )
    return gateway, calls


def test_postgres_gateway_exposes_contract_and_preserves_decimal():
    gateway, calls = _gateway()
    assert gateway.descriptor.backend == "postgres"
    assert gateway.descriptor.dialect == "postgres"
    assert gateway.test_connection().status == "healthy"
    assert gateway.schema() == {"sales": ["amount", "id"]}
    result = gateway.query(GatewayQuery(sql="SELECT amount FROM sales", params={"region": "east"}, max_rows=20, offset=3))
    assert result.rows == [[Decimal("10.50")]]
    assert calls[-1] == ("SELECT amount FROM sales LIMIT 24", {"region": "east"}, 20, 3)


def test_postgres_gateway_preview_quotes_only_allowed_table():
    gateway, calls = _gateway()
    gateway.preview("sales", limit=8)
    assert calls[-1][0] == 'SELECT * FROM "sales" LIMIT 9'
    with pytest.raises(GatewayError) as exc:
        gateway.preview("users")
    assert exc.value.code == "E_GATEWAY_TABLE"


def test_postgres_gateway_hides_internal_tables_and_connection_details():
    gateway = PostgreSQLGateway(
        gateway_id="postgres-main",
        name="PostgreSQL Main",
        schema_loader=lambda: {"sales": {"id"}, "users": {"password_hash"}},
        executor=lambda *_args: ([], [], False),
        connect=lambda: (_ for _ in ()).throw(RuntimeError("password=secret host=db.internal")),
    )
    assert gateway.schema() == {"sales": ["id"]}
    health = gateway.health()
    assert health.status == "unavailable"
    assert "secret" not in health.message and "db.internal" not in health.message


@pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="set RUN_POSTGRES_TESTS=1 to run against local PostgreSQL")
def test_postgres_gateway_against_readonly_database():
    gateway = PostgreSQLGateway(gateway_id="postgres-main", name="PostgreSQL Main")
    assert gateway.health().status == "healthy"
    assert {"sales", "customers"} <= set(gateway.schema())
    preview = gateway.preview("sales", limit=2)
    assert preview.columns and preview.row_count <= 2
    result = gateway.query(GatewayQuery(sql="SELECT amount FROM sales ORDER BY id", max_rows=2))
    assert result.columns == ["amount"]
    assert all(isinstance(row[0], Decimal) for row in result.rows)
    with pytest.raises(Exception):
        gateway.query(GatewayQuery(sql="CREATE TABLE gateway_write_probe (id INTEGER)"))
