from decimal import Decimal

import pytest
from sqlglot import exp

from app.domain.data_gateway import GatewayError, GatewayQuery
from app.gateways.clickhouse_gateway import ClickHouseGateway
from app.gateways.oracle_gateway import OracleGateway
from app.gateways.sqlserver_gateway import SQLServerGateway
from app.gateways.remote_sql import compile_remote_sql, normalize_remote_sql, select_tree
from app.guardrails import QueryError


GATEWAYS = [OracleGateway, SQLServerGateway, ClickHouseGateway]


class Cursor:
    description = [("amount",)]

    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def execute(self, sql, params=None, **options):
        self.connection.calls.append((sql, params))
        self.connection.execute_options = options
        if sql == self.connection.schema_sql:
            self.rows = [("sales", "amount"), ("sales", "region"), ("users", "password_hash")]
        else:
            self.rows = [(Decimal("1.25"),), (Decimal("2.50"),), (Decimal("3.75"),)]

    def fetchone(self):
        return self.fetchmany(1)[0]

    def fetchmany(self, size):
        rows, self.rows = self.rows[:size], self.rows[size:]
        return rows

    def fetchall(self):
        return self.fetchmany(len(self.rows))

    def close(self):
        self.connection.cursor_closed = True


class Connection:
    def __init__(self, schema_sql, calls):
        self.schema_sql = schema_sql
        self.calls = calls
        self.closed = False
        self.cursor_closed = False

    def cursor(self):
        return Cursor(self)

    def close(self):
        self.closed = True


def gateway(gateway_type):
    connections, calls = [], []

    def connect():
        connection = Connection(gateway_type.schema_sql, calls)
        connections.append(connection)
        return connection

    instance = gateway_type(gateway_id="remote", name="Remote", connect=connect)
    return instance, connections, calls


@pytest.mark.parametrize("gateway_type", GATEWAYS)
def test_remote_contract_health_schema_query_and_cleanup(gateway_type):
    instance, connections, calls = gateway(gateway_type)
    assert instance.health().status == "healthy"
    assert instance.schema() == {"sales": ["amount", "region"]}
    assert instance.descriptor.metadata["maturity"] == "experimental"
    result = instance.query(GatewayQuery("SELECT amount FROM sales WHERE region=:region", {"region": "east"}, max_rows=1, offset=1, operation_id="query-1"))
    assert result.rows == [[Decimal("2.50")]] and result.truncated
    assert result.operation_id == "query-1"
    assert calls[-1][1] == {"region": "east"}
    sql = calls[-1][0]
    assert (":region" if instance.backend == "oracle" else "%(region)s") in sql
    assert all(connection.closed and connection.cursor_closed for connection in connections)


@pytest.mark.parametrize("gateway_type", GATEWAYS)
def test_remote_non_recursive_cte_preaggregation_round_trips(gateway_type):
    instance, _, calls = gateway(gateway_type)
    sql = (
        "WITH regional AS ("
        "SELECT s.region AS region_value, SUM(s.amount) AS total_amount "
        "FROM sales s GROUP BY s.region"
        ") SELECT r.total_amount FROM regional r WHERE r.region_value=:region"
    )

    result = instance.query(GatewayQuery(sql, {"region": "east"}, max_rows=2))

    assert result.rows == [[Decimal("1.25")], [Decimal("2.50")]]
    rendered, params = calls[-1]
    assert "WITH" in rendered.upper()
    assert params == {"region": "east"}


@pytest.mark.parametrize("gateway_type", GATEWAYS)
def test_remote_preview_and_empty_allowlist(gateway_type):
    instance, _, calls = gateway(gateway_type)
    assert instance.preview("sales", limit=1).row_count == 1
    assert "sales" in calls[-1][0]
    with pytest.raises(GatewayError) as error:
        instance.preview("users")
    assert error.value.code == "E_GATEWAY_TABLE"
    instance.allowed.clear()
    assert instance.schema() == {}


@pytest.mark.parametrize("gateway_type", GATEWAYS)
@pytest.mark.parametrize("sql", [
    "DELETE FROM sales", "SELECT amount FROM sales; DROP TABLE sales",
    "SELECT * INTO copied FROM sales", "SELECT amount FROM sales FOR UPDATE",
    "SELECT amount FROM other.sales", "SELECT amount FROM sales@remote",
    "SELECT * FROM remote('host', 'db', 'sales')", "SELECT readfile('/secret') FROM sales",
    "SELECT dangerous_function(amount) FROM sales", "SELECT sequence.NEXTVAL FROM sales",
    "WITH RECURSIVE loop AS (SELECT amount FROM sales) SELECT amount FROM loop",
    "SELECT amount FROM users", "SELECT password_hash FROM sales",
])
def test_remote_rejects_unsafe_sql_before_business_execution(gateway_type, sql):
    instance, _, calls = gateway(gateway_type)
    with pytest.raises(GatewayError):
        instance.query(GatewayQuery(sql))
    assert all(statement == instance.schema_sql for statement, _ in calls)


@pytest.mark.parametrize("gateway_type", GATEWAYS)
def test_remote_safe_failure(gateway_type):
    instance = gateway_type(gateway_id="remote", name="Remote", connect=lambda: (_ for _ in ()).throw(RuntimeError("password=secret host=internal")))
    assert instance.health().status == "unavailable"
    with pytest.raises(GatewayError) as error:
        instance.schema()
    assert "secret" not in str(error.value)
    assert "internal" not in instance.health().message


@pytest.mark.parametrize("backend,native", [
    ("oracle", "SELECT amount FROM sales FETCH FIRST 2 ROWS ONLY"),
    ("sqlserver", "SELECT TOP 2 amount FROM sales"),
    ("clickhouse", "SELECT amount FROM sales LIMIT 2"),
])
def test_native_sql_normalizes_for_policy_and_round_trips(backend, native):
    canonical = normalize_remote_sql(native, backend)
    assert "LIMIT 2" in canonical
    output = compile_remote_sql(select_tree(canonical, "sqlite"), backend, {})
    assert "TOP 2" in output if backend == "sqlserver" else "2" in output


@pytest.mark.parametrize("backend", ["oracle", "sqlserver", "clickhouse"])
def test_bound_values_never_enter_sql_and_literals_are_not_parameters(backend):
    tree = select_tree("SELECT ':region' AS label, amount FROM sales WHERE region=:region AND region LIKE '%east%'", "sqlite")
    sql = compile_remote_sql(tree, backend, {"region": "x'; DROP TABLE sales;--"})
    assert "DROP TABLE" not in sql
    assert "':region'" in sql
    assert ("'%%east%%'" if backend == "clickhouse" else "'%east%'") in sql
    with pytest.raises(QueryError, match="Missing query parameter"):
        compile_remote_sql(tree, backend, {})


def test_remote_role_denial_happens_before_opening_connection():
    from app.gateways.registry import GatewayRegistry
    from app.services.data_gateway_service import DataGatewayService
    instance, connections, _ = gateway(OracleGateway)
    service = DataGatewayService(GatewayRegistry([instance]), can_use=lambda i, d: "analyst" in i["roles"])
    with pytest.raises(GatewayError) as error:
        service.query({"roles": ["viewer"]}, "remote", GatewayQuery("SELECT amount FROM sales"))
    assert error.value.code == "E_GATEWAY_FORBIDDEN" and not connections


@pytest.mark.parametrize("gateway_type", GATEWAYS)
def test_factory_lists_experimental_connector_without_drivers(gateway_type):
    from app.gateways.factory import build_gateway_registry
    from app import datasources
    instance = build_gateway_registry([{"id": "remote", "backend": gateway_type.backend}]).require("remote")
    assert instance.descriptor.backend == gateway_type.backend
    assert instance.descriptor.metadata["external_validation"] == "not_run"
    assert gateway_type.backend in datasources.SUPPORTED_BACKENDS


def test_factory_passes_oracle_schema_owner_to_gateway(monkeypatch):
    from app.config import settings
    from app.gateways.factory import build_gateway_registry

    monkeypatch.setattr(settings, "oracle_plan_user", "planner")
    monkeypatch.setattr(settings, "oracle_plan_password", "planner-secret")

    instance = build_gateway_registry(
        [{
            "id": "oracle",
            "backend": "oracle",
            "schema_owner": "querypilot_owner",
            "cost_estimate_enabled": True,
        }]
    ).require("oracle")

    assert instance.schema_owner == "QUERYPILOT_OWNER"
    assert instance.capabilities.cost_estimate is True


@pytest.mark.parametrize("gateway_type", GATEWAYS)
def test_invalid_bounds_and_missing_binds_fail_without_query(gateway_type):
    instance, connections, calls = gateway(gateway_type)
    with pytest.raises(GatewayError) as error:
        instance.query(GatewayQuery("SELECT amount FROM sales", max_rows=10001))
    assert error.value.code == "E_GATEWAY_LIMIT" and not connections
    with pytest.raises(GatewayError):
        instance.query(GatewayQuery("SELECT amount FROM sales WHERE region=:region"))
    assert all(sql == instance.schema_sql for sql, _ in calls)


def test_sqlserver_driver_preserves_literals_and_binds_without_a_server():
    driver = pytest.importorskip("pymssql._mssql")
    tree = select_tree("SELECT ':region', amount % 2 FROM sales WHERE region=:region AND region LIKE '%east%'", "sqlite")
    params = {"region": "east' OR 1=1 --"}
    sql = compile_remote_sql(tree, "sqlserver", params)
    bound = driver.substitute_params(sql, params).decode()
    assert "':region'" in bound and "LIKE '%east%'" in bound
    assert "N'east'' OR 1=1 --'" in bound
    with pytest.raises(QueryError):
        compile_remote_sql(select_tree("SELECT '%(region)s' FROM sales", "sqlite"), "sqlserver", params)


def test_clickhouse_connection_does_not_combine_readonly_with_timeout(monkeypatch):
    """ClickHouse rejects every setting change while readonly=1 is active.

    Sending both flags made every query fail on a read-only acceptance account.
    Read-only must come from the server grants, not from a client setting.
    """
    clickhouse_driver = pytest.importorskip("clickhouse_driver")
    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(clickhouse_driver.dbapi, "connect", fake_connect)
    from app.config import settings

    monkeypatch.setattr(settings, "clickhouse_host", "127.0.0.1")
    monkeypatch.setattr(settings, "clickhouse_user", "reader")
    monkeypatch.setattr(settings, "clickhouse_password", "secret")
    monkeypatch.setattr(settings, "clickhouse_database", "demo")

    ClickHouseGateway.default_connect()

    assert "readonly" not in captured["settings"]
    assert "max_execution_time" in captured["settings"]


def test_clickhouse_driver_preserves_literals_and_binds_without_a_server():
    driver = pytest.importorskip("clickhouse_driver")
    client = driver.Client("localhost")
    params = {"region": "east' OR 1=1 --"}
    tree = select_tree("SELECT '%(region)s', amount % 2 FROM sales WHERE region=:region AND region LIKE '%east%'", "sqlite")
    sql = compile_remote_sql(tree, "clickhouse", params)
    bound = client.substitute_params(sql, params, client.connection.context)
    assert "'%(region)s'" in bound and "LIKE '%east%'" in bound
    assert "amount % 2" in bound and "east\\' OR 1=1 --" in bound


@pytest.mark.parametrize("gateway_type,native", [
    (OracleGateway, "SELECT amount FROM sales FETCH FIRST 2 ROWS ONLY"),
    (SQLServerGateway, "SELECT TOP 2 amount FROM sales"),
    (ClickHouseGateway, "SELECT amount FROM sales LIMIT 2"),
])
def test_remote_main_query_and_readonly_planning_keep_identity_scope(monkeypatch, gateway_type, native):
    from fastapi.testclient import TestClient
    from app import datasources, main
    from app.auth import require_identity
    instance, _, calls = gateway(gateway_type)
    monkeypatch.setattr(gateway_type, "default_connect", staticmethod(instance.connect))
    monkeypatch.setattr(datasources, "load_registry", lambda: {"datasources": [{"id": "remote", "backend": gateway_type.backend, "roles": ["analyst"]}]})
    main.app.dependency_overrides[require_identity] = lambda: {"subject": "remote-test", "tenant_id": "default", "roles": ["analyst"]}
    try:
        client = TestClient(main.app)
        response = client.post("/api/v1/query", json={"question": "remote query", "sql": native, "datasource": "remote"})
        assert response.status_code == 200, response.text
        assert response.json()["columns"] == ["amount"]
        count = len(calls)
        planned = client.post("/api/v1/integrations/read-only/call", json={"tool": "query.plan", "arguments": {"question": "remote plan", "sql": native, "datasource_id": "remote"}})
        assert planned.status_code == 200, planned.text
        assert planned.json()["result"]["validation"] == "passed"
        assert all(sql == instance.schema_sql for sql, _ in calls[count:])
        main.app.dependency_overrides[require_identity] = lambda: {"subject": "viewer", "tenant_id": "default", "roles": ["viewer"]}
        denied = client.post("/api/v1/query", json={"question": "denied", "sql": native, "datasource": "remote"})
        assert denied.status_code == 403
    finally:
        main.app.dependency_overrides.pop(require_identity, None)


@pytest.mark.parametrize("gateway_type", GATEWAYS)
def test_query_failure_closes_connections_and_hides_driver_error(gateway_type):
    instance, connections, _ = gateway(gateway_type)

    def fail(*args):
        raise RuntimeError("password=secret")

    instance.execute_query = fail
    with pytest.raises(GatewayError) as error:
        instance.query(GatewayQuery("SELECT amount FROM sales"))
    assert error.value.code == "E_GATEWAY_QUERY" and "secret" not in str(error.value)
    assert all(connection.closed and connection.cursor_closed for connection in connections)


def test_oracle_requests_decimal_preservation_without_global_driver_mutation():
    instance, connections, calls = gateway(OracleGateway)
    instance.query(GatewayQuery("SELECT amount FROM sales"))
    assert connections[-1].execute_options == {"fetch_decimals": True}
    assert any(sql == "SET TRANSACTION READ ONLY" for sql, _ in calls)


@pytest.mark.parametrize("sql", [
    "SELECT amount FROM sales OFFSET 100001",
    "SELECT amount FROM sales LIMIT :limit",
    "SELECT amount FROM sales LIMIT -1",
    "SELECT amount FROM sales SETTINGS readonly=0",
])
def test_query_cannot_override_execution_limits(sql):
    instance, _, calls = gateway(ClickHouseGateway)
    with pytest.raises(GatewayError):
        instance.query(GatewayQuery(sql, {"limit": 100000}))
    assert all(statement == instance.schema_sql for statement, _ in calls)


@pytest.mark.parametrize("gateway_type", [OracleGateway, SQLServerGateway])
def test_remote_schema_details_groups_and_filters_database_relationships(gateway_type):
    class RelationshipCursor:
        description = []

        def __init__(self, connection):
            self.connection = connection
            self.rows = []

        def execute(self, sql, params=None, **options):
            self.connection.calls.append((sql, params))
            if sql == gateway_type.schema_sql:
                self.rows = [
                    ("sales", "customer_id"),
                    ("customers", "id"),
                    ("private", "id"),
                ]
            elif sql == gateway_type.relationship_sql:
                self.rows = [
                    ("sales", "customer_id", "customers", "id", "fk_sales_customer", 1),
                    ("private", "id", "customers", "id", "fk_private_customer", 1),
                ]
            else:
                self.rows = []

        def fetchall(self):
            rows, self.rows = self.rows, []
            return rows

        def close(self):
            self.connection.cursor_closed = True

    class RelationshipConnection:
        def __init__(self):
            self.calls = []
            self.closed = False
            self.cursor_closed = False

        def cursor(self):
            return RelationshipCursor(self)

        def close(self):
            self.closed = True

    connections = []

    def connect():
        connection = RelationshipConnection()
        connections.append(connection)
        return connection

    instance = gateway_type(gateway_id="remote", name="Remote", allowed_tables=["sales", "customers"], connect=connect)
    details = instance.schema_details()

    assert details.tables == {"sales": ["customer_id"], "customers": ["id"]}
    assert len(details.relationships) == 1
    assert details.relationships[0].from_table == "sales"
    assert details.relationships[0].from_columns == ["customer_id"]
    assert details.relationships[0].to_table == "customers"
    assert details.relationships[0].to_columns == ["id"]
    assert all(connection.closed and connection.cursor_closed for connection in connections)

    from app.gateways.registry import GatewayRegistry
    from app.services.data_gateway_service import DataGatewayService

    service = DataGatewayService(GatewayRegistry([instance]), can_use=lambda identity, descriptor: True)
    exposed = service.schema_details({}, "remote")
    assert exposed.relationships[0].from_table == "sales"
    assert exposed.tables == details.tables


def test_oracle_schema_owner_reads_all_metadata_and_switches_query_schema():
    class OwnerCursor:
        description = [("amount",)]

        def __init__(self, connection):
            self.connection = connection
            self.rows = []

        def execute(self, sql, params=None, **options):
            self.connection.calls.append((sql, params, options))
            normalized = " ".join(sql.split()).upper()
            if "FROM ALL_TAB_COLUMNS" in normalized:
                assert params == {"schema_owner": "QUERYPILOT_OWNER"}
                self.rows = [
                    ("CUSTOMERS", "ID"),
                    ("SALES", "ID"),
                    ("SALES", "CUSTOMER_ID"),
                    ("SALES", "AMOUNT"),
                ]
            elif "FROM ALL_CONSTRAINTS" in normalized:
                assert params == {"schema_owner": "QUERYPILOT_OWNER"}
                self.rows = [
                    ("SALES", "CUSTOMER_ID", "CUSTOMERS", "ID", "FK_SALES_CUSTOMER", 1)
                ]
            elif normalized.startswith("SELECT"):
                self.rows = [(Decimal("12.50"),)]
            else:
                self.rows = []

        def fetchone(self):
            return self.rows.pop(0)

        def fetchmany(self, size):
            rows, self.rows = self.rows[:size], self.rows[size:]
            return rows

        def fetchall(self):
            rows, self.rows = self.rows, []
            return rows

        def close(self):
            self.connection.cursor_closed = True

    class OwnerConnection:
        def __init__(self):
            self.calls = []
            self.closed = False
            self.cursor_closed = False

        def cursor(self):
            return OwnerCursor(self)

        def close(self):
            self.closed = True

    connections = []

    def connect():
        connection = OwnerConnection()
        connections.append(connection)
        return connection

    instance = OracleGateway(
        gateway_id="oracle",
        name="Oracle",
        allowed_tables=["sales", "customers"],
        schema_owner="querypilot_owner",
        connect=connect,
    )

    details = instance.schema_details()
    result = instance.query(GatewayQuery("SELECT amount FROM sales", max_rows=1))

    assert set(details.tables) == {"CUSTOMERS", "SALES"}
    assert len(details.relationships) == 1
    assert details.relationships[0].from_table == "SALES"
    assert details.relationships[0].to_table == "CUSTOMERS"
    assert result.rows == [[Decimal("12.50")]]
    executed = [" ".join(sql.split()).upper() for connection in connections for sql, _, _ in connection.calls]
    assert 'ALTER SESSION SET CURRENT_SCHEMA = "QUERYPILOT_OWNER"' in executed
    assert "SET TRANSACTION READ ONLY" in executed
    assert all(connection.closed and connection.cursor_closed for connection in connections)


@pytest.mark.parametrize("schema_owner", ["owner; DROP USER x", "owner.name", 'owner"x', ""])
def test_oracle_schema_owner_rejects_unsafe_identifiers(schema_owner):
    with pytest.raises(GatewayError) as error:
        OracleGateway(gateway_id="oracle", name="Oracle", schema_owner=schema_owner)

    assert error.value.code == "E_GATEWAY_CONFIG"


def test_oracle_cost_estimate_uses_separate_planner_and_cleans_plan_rows():
    class QueryCursor:
        description = []

        def __init__(self):
            self.rows = []

        def execute(self, sql, params=None, **options):
            if "ALL_TAB_COLUMNS" in sql.upper():
                self.rows = [("SALES", "ID"), ("SALES", "REGION"), ("SALES", "AMOUNT")]

        def fetchall(self):
            rows, self.rows = self.rows, []
            return rows

        def close(self):
            pass

    class QueryConnection:
        def cursor(self):
            return QueryCursor()

        def close(self):
            pass

    class PlanCursor:
        description = []

        def __init__(self, connection):
            self.connection = connection
            self.rows = []

        def execute(self, sql, params=None, **options):
            self.connection.calls.append((sql, params))
            normalized = " ".join(sql.split()).upper()
            if normalized.startswith("SELECT ID, OPERATION"):
                self.rows = [
                    (0, "SELECT STATEMENT", None, None, 3, 7),
                    (1, "TABLE ACCESS", "FULL", "SALES", 3, 7),
                ]

        def fetchall(self):
            rows, self.rows = self.rows, []
            return rows

        def close(self):
            self.connection.cursor_closed = True

    class PlanConnection:
        def __init__(self):
            self.calls = []
            self.commits = 0
            self.closed = False
            self.cursor_closed = False

        def cursor(self):
            return PlanCursor(self)

        def commit(self):
            self.commits += 1

        def close(self):
            self.closed = True

    planner_connections = []

    def plan_connect():
        connection = PlanConnection()
        planner_connections.append(connection)
        return connection

    gateway = OracleGateway(
        gateway_id="oracle",
        name="Oracle",
        allowed_tables=["sales"],
        schema_owner="querypilot_owner",
        connect=QueryConnection,
        plan_connect=plan_connect,
    )

    estimate = gateway.estimate_cost(
        GatewayQuery(
            "SELECT amount FROM sales WHERE region=:region",
            {"region": "华东"},
            max_rows=20,
        )
    )

    assert gateway.capabilities.cost_estimate is True
    assert estimate.supported is True
    assert estimate.estimated_rows == 3
    assert estimate.estimated_cost == 7
    assert estimate.full_scan is True
    assert estimate.index_used is False
    assert estimate.plan["operations"][1]["object_name"] == "SALES"
    connection = planner_connections[0]
    statements = [" ".join(sql.split()).upper() for sql, _ in connection.calls]
    assert 'ALTER SESSION SET CURRENT_SCHEMA = "QUERYPILOT_OWNER"' in statements
    assert any(statement.startswith("EXPLAIN PLAN SET STATEMENT_ID") for statement in statements)
    assert sum("DELETE FROM PLAN_TABLE" in statement for statement in statements) == 2
    assert connection.commits == 1
    assert connection.closed and connection.cursor_closed


def test_oracle_cost_estimate_rejects_writes_before_planner_connection():
    planner_calls = []
    gateway = OracleGateway(
        gateway_id="oracle",
        name="Oracle",
        allowed_tables=["sales"],
        connect=lambda: pytest.fail("schema connection must not open for a write"),
        plan_connect=lambda: planner_calls.append(True),
    )

    with pytest.raises(GatewayError) as error:
        gateway.estimate_cost(GatewayQuery("DELETE FROM sales"))

    assert error.value.code == "E_READONLY_VIOLATION"
    assert planner_calls == []


def test_oracle_cost_estimate_hides_planner_errors():
    class BrokenConnection:
        def cursor(self):
            raise RuntimeError("password=planner-secret host=oracle.internal")

        def close(self):
            pass

    gateway = OracleGateway(
        gateway_id="oracle",
        name="Oracle",
        allowed_tables=["sales"],
        connect=lambda: pytest.fail("schema lookup is replaced below"),
        plan_connect=BrokenConnection,
    )
    gateway.schema = lambda: {"sales": ["amount"]}

    with pytest.raises(GatewayError) as error:
        gateway.estimate_cost(GatewayQuery("SELECT amount FROM sales"))

    assert error.value.code == "E_GATEWAY_COST"
    assert "planner-secret" not in str(error.value)
    assert "oracle.internal" not in str(error.value)


@pytest.mark.parametrize("gateway_type", GATEWAYS)
def test_remote_cost_contract_is_explicitly_unsupported_until_vendor_explain_is_verified(gateway_type):
    instance, _, _ = gateway(gateway_type)

    estimate = instance.estimate_cost(GatewayQuery("SELECT amount FROM sales"))

    assert estimate.backend == gateway_type.backend
    assert estimate.supported is False
    assert "not implemented" in estimate.reason
    assert instance.capabilities.cost_estimate is False
