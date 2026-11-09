import pytest

from app.domain.data_gateway import GatewayError, GatewayQuery
from app.gateways.mysql_gateway import MySQLGateway


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
    gateway = MySQLGateway(
        gateway_id="mysql-main",
        name="MySQL Main",
        schema_loader=lambda: {"sales": {"id", "amount"}},
        executor=lambda sql, params, max_rows, offset: calls.append((sql, params, max_rows, offset)) or (["amount"], [[10]], False),
        connect=lambda: _Connection(),
    )
    return gateway, calls


def test_mysql_gateway_exposes_contract_and_executes_query():
    gateway, calls = _gateway()
    assert gateway.descriptor.backend == "mysql"
    assert gateway.descriptor.dialect == "mysql"
    assert gateway.test_connection().status == "healthy"
    assert gateway.schema() == {"sales": ["amount", "id"]}
    result = gateway.query(GatewayQuery(sql="SELECT amount FROM sales", params={"region": "east"}, max_rows=20, offset=3))
    assert result.columns == ["amount"] and result.rows == [[10]]
    assert calls[-1] == ("SELECT amount FROM sales LIMIT 24", {"region": "east"}, 20, 3)


def test_mysql_gateway_preview_quotes_only_known_table():
    gateway, calls = _gateway()
    gateway.preview("sales", limit=8)
    assert calls[-1][0] == "SELECT * FROM `sales` LIMIT 9"
    with pytest.raises(GatewayError) as exc:
        gateway.preview("unknown")
    assert exc.value.code == "E_GATEWAY_TABLE"


def test_mysql_gateway_converts_driver_failure_to_safe_health():
    gateway = MySQLGateway(gateway_id="mysql-main", name="MySQL Main", schema_loader=lambda: {}, executor=lambda *_args: ([], [], False), connect=lambda: (_ for _ in ()).throw(RuntimeError("password=secret host=db.internal")))
    health = gateway.health()
    assert health.status == "unavailable"
    assert "secret" not in health.message and "db.internal" not in health.message


def test_mysql_gateway_never_exposes_internal_tables():
    gateway = MySQLGateway(gateway_id="mysql-main", name="MySQL Main", schema_loader=lambda: {"sales": {"id"}, "users": {"password_hash"}}, executor=lambda *_args: ([], [], False), connect=lambda: _Connection())
    assert gateway.schema() == {"sales": ["id"]}
    with pytest.raises(GatewayError):
        gateway.preview("users")
