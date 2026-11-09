"""Opt-in probes for dedicated, pre-provisioned read-only acceptance databases."""
import os
from contextlib import closing

import pytest
from sqlglot import exp

from app.domain.data_gateway import GatewayError, GatewayQuery
from app.gateways.oracle_gateway import OracleGateway
from app.gateways.sqlserver_gateway import SQLServerGateway
from app.gateways.clickhouse_gateway import ClickHouseGateway


CONNECTORS = [
    pytest.param(OracleGateway, "RUN_ORACLE_TESTS", id="oracle"),
    pytest.param(SQLServerGateway, "RUN_SQLSERVER_TESTS", id="sqlserver"),
    pytest.param(ClickHouseGateway, "RUN_CLICKHOUSE_TESTS", id="clickhouse"),
]


def _gateway(gateway_type, flag):
    if os.getenv(flag) != "1":
        pytest.skip(f"Set {flag}=1 for a dedicated acceptance database")
    options = {"allowed_tables": ["sales", "customers"]}
    if gateway_type is OracleGateway:
        options["schema_owner"] = os.getenv("ORACLE_SCHEMA_OWNER")
        options["cost_estimate_enabled"] = True
    return gateway_type(gateway_id="acceptance", name="Acceptance", **options)


def _sales_schema(gateway):
    schema = gateway.schema()
    table = next((name for name in schema if name.lower() == "sales"), None)
    assert table is not None, "Acceptance database needs a sales table"
    columns = {column.lower(): column for column in schema[table]}
    assert {"id", "region", "amount"} <= columns.keys()
    return schema, table, columns


@pytest.mark.parametrize("gateway_type,flag", CONNECTORS)
def test_remote_health(gateway_type, flag):
    gateway = _gateway(gateway_type, flag)
    assert gateway.health().status == "healthy"


@pytest.mark.parametrize("gateway_type,flag", CONNECTORS)
def test_remote_schema(gateway_type, flag):
    gateway = _gateway(gateway_type, flag)
    _sales_schema(gateway)


@pytest.mark.parametrize("gateway_type,flag", CONNECTORS)
def test_remote_schema_details(gateway_type, flag):
    gateway = _gateway(gateway_type, flag)
    schema, _, _ = _sales_schema(gateway)
    details = gateway.schema_details()
    assert set(details.tables) == set(schema)
    if gateway.capabilities.relationships:
        assert details.relationships, "Acceptance database must expose at least one allowed foreign-key relationship"
        assert all(
            relation.from_table in details.tables and relation.to_table in details.tables
            for relation in details.relationships
        )


@pytest.mark.parametrize("gateway_type,flag", CONNECTORS)
def test_remote_preview(gateway_type, flag):
    gateway = _gateway(gateway_type, flag)
    _, table, _ = _sales_schema(gateway)
    preview = gateway.preview(table, limit=1)
    assert preview.row_count == 1 and preview.truncated, "Seed at least two sales rows"


@pytest.mark.parametrize("gateway_type,flag", CONNECTORS)
def test_remote_parameter_binding(gateway_type, flag):
    gateway = _gateway(gateway_type, flag)
    _, table, columns = _sales_schema(gateway)
    preview = gateway.preview(table, limit=1)
    table_sql = exp.to_identifier(table, quoted=True).sql(dialect="sqlite")
    region_sql = exp.to_identifier(columns["region"], quoted=True).sql(dialect="sqlite")
    amount_sql = exp.to_identifier(columns["amount"], quoted=True).sql(dialect="sqlite")
    region = preview.rows[0][preview.columns.index(columns["region"])]
    result = gateway.query(GatewayQuery(f"SELECT {region_sql}, {amount_sql} FROM {table_sql} WHERE {region_sql}=:region", {"region": region}, max_rows=2))
    assert result.rows and all(row[0] == region for row in result.rows)


@pytest.mark.parametrize("gateway_type,flag", CONNECTORS)
def test_remote_bounded_query(gateway_type, flag):
    gateway = _gateway(gateway_type, flag)
    _, table, columns = _sales_schema(gateway)
    table_sql = exp.to_identifier(table, quoted=True).sql(dialect="sqlite")
    amount_sql = exp.to_identifier(columns["amount"], quoted=True).sql(dialect="sqlite")
    result = gateway.query(GatewayQuery(f"SELECT {amount_sql} FROM {table_sql}", max_rows=1))
    assert result.row_count == 1 and result.truncated


@pytest.mark.parametrize("gateway_type,flag", CONNECTORS)
def test_remote_cte_preaggregation(gateway_type, flag):
    gateway = _gateway(gateway_type, flag)
    _, table, columns = _sales_schema(gateway)
    preview = gateway.preview(table, limit=1)
    table_sql = exp.to_identifier(table, quoted=True).sql(dialect="sqlite")
    region_sql = exp.to_identifier(columns["region"], quoted=True).sql(dialect="sqlite")
    amount_sql = exp.to_identifier(columns["amount"], quoted=True).sql(dialect="sqlite")
    region = preview.rows[0][preview.columns.index(columns["region"])]
    cte_sql = (
        f"WITH regional AS (SELECT {region_sql} AS region_value, "
        f"SUM({amount_sql}) AS total_amount FROM {table_sql} GROUP BY {region_sql}) "
        "SELECT total_amount FROM regional WHERE region_value=:region"
    )
    cte_result = gateway.query(GatewayQuery(cte_sql, {"region": region}, max_rows=2))
    assert cte_result.rows, "Non-recursive CTE preaggregation must execute on the target database"


@pytest.mark.parametrize(
    "gateway_type,flag",
    [pytest.param(OracleGateway, "RUN_ORACLE_TESTS", id="oracle")],
)
def test_remote_cost_estimate(gateway_type, flag):
    gateway = _gateway(gateway_type, flag)
    _, table, columns = _sales_schema(gateway)
    table_sql = exp.to_identifier(table, quoted=True).sql(dialect="sqlite")
    region_sql = exp.to_identifier(columns["region"], quoted=True).sql(dialect="sqlite")
    amount_sql = exp.to_identifier(columns["amount"], quoted=True).sql(dialect="sqlite")
    preview = gateway.preview(table, limit=1)
    region = preview.rows[0][preview.columns.index(columns["region"])]

    estimate = gateway.estimate_cost(
        GatewayQuery(
            f"SELECT {amount_sql} FROM {table_sql} WHERE {region_sql}=:region",
            {"region": region},
            max_rows=10,
        )
    )

    assert estimate.supported is True
    assert estimate.estimated_cost is not None and estimate.estimated_cost >= 0
    assert estimate.plan.get("operations")


@pytest.mark.parametrize("gateway_type,flag", CONNECTORS)
def test_remote_readonly_rejection(gateway_type, flag):
    gateway = _gateway(gateway_type, flag)
    _, table, columns = _sales_schema(gateway)
    table_sql = exp.to_identifier(table, quoted=True).sql(dialect="sqlite")
    with pytest.raises(GatewayError) as rejected:
        gateway.query(GatewayQuery(f"DELETE FROM {table_sql}"))
    assert rejected.value.code == "E_READONLY_VIOLATION"
    with pytest.raises(GatewayError):
        gateway.preview("not_authorized")

    # Zero-row write verifies database enforcement without changing business rows.
    native_table = exp.to_identifier(table, quoted=True).sql(dialect=gateway.descriptor.dialect)
    native_id = exp.to_identifier(columns["id"], quoted=True).sql(dialect=gateway.descriptor.dialect)
    write = f"UPDATE {native_table} SET {native_id}={native_id} WHERE 1=0"
    if gateway.backend == "clickhouse":
        write = f"INSERT INTO {native_table} SELECT * FROM {native_table} WHERE 1=0"
    with closing(gateway.default_connect()) as connection, closing(connection.cursor()) as cursor:
        try:
            cursor.execute(write)
        except Exception as exc:
            if gateway.backend == "oracle":
                code = getattr(exc.args[0], "code", None)
                assert code in {1031, 16000}, "Expected an Oracle privilege/read-only rejection"
            elif gateway.backend == "sqlserver":
                assert exc.args[0] in {229, 3906}, "Expected a SQL Server privilege/read-only rejection"
            else:
                underlying = exc.args[0] if exc.args else None
                assert getattr(underlying, "code", None) in {164, 497}, "Expected a ClickHouse privilege/read-only rejection"
        else:
            pytest.fail("Database accepted a write probe; configure a read-only account")
