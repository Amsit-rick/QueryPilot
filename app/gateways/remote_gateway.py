"""Shared bounded DB-API operations for experimental remote gateways."""
from contextlib import closing

from sqlglot import exp

from app.domain.data_gateway import (
    GatewayCapabilities,
    GatewayCostEstimate,
    GatewayDescriptor,
    GatewayError,
    GatewayHealth,
    GatewayQuery,
    GatewayRelationship,
    GatewayResult,
    GatewaySchemaDetails,
)
from app.guardrails import QueryError, validate_sql
from app.gateways.remote_sql import REMOTE_DIALECTS, compile_remote_sql, select_tree


class RemoteGateway:
    capabilities = GatewayCapabilities(cancel=False)
    backend = ""
    health_sql = "SELECT 1"
    schema_sql = ""
    relationship_sql = ""

    def __init__(self, *, gateway_id, name, allowed_tables=None, connect=None):
        if allowed_tables is not None and (
            not isinstance(allowed_tables, list)
            or any(not isinstance(table, str) or not table.strip() for table in allowed_tables)
        ):
            raise GatewayError("E_GATEWAY_CONFIG", "allowed_tables must be a list of table names", operation="configure")
        self.allowed = {name.lower() for name in (allowed_tables if allowed_tables is not None else ["sales", "customers"])}
        self.connect = connect or self.default_connect
        self.descriptor = GatewayDescriptor(
            gateway_id, name, self.backend, REMOTE_DIALECTS[self.backend],
            metadata={"maturity": "experimental", "external_validation": "not_run"},
        )

    def test_connection(self):
        try:
            with closing(self.connect()) as connection, closing(connection.cursor()) as cursor:
                cursor.execute(self.health_sql)
                cursor.fetchone()
            return GatewayHealth("healthy", "Connection succeeded; compatibility acceptance is separate")
        except Exception:
            return GatewayHealth("unavailable", "Connector unavailable; check optional driver and local configuration")

    health = test_connection

    def schema(self):
        try:
            with closing(self.connect()) as connection, closing(connection.cursor()) as cursor:
                params = self.schema_query_params()
                if params is None:
                    cursor.execute(self.schema_sql)
                else:
                    cursor.execute(self.schema_sql, params)
                result = {}
                for table, column in cursor.fetchall():
                    if table.lower() in self.allowed:
                        result.setdefault(table, []).append(column)
                return result
        except Exception as exc:
            raise GatewayError("E_GATEWAY_SCHEMA", "Connector schema unavailable", operation="schema") from exc

    def schema_details(self):
        """Return visible columns plus database-declared foreign keys."""
        tables = self.schema()
        if not self.relationship_sql:
            return GatewaySchemaDetails(tables, [])
        try:
            with closing(self.connect()) as connection, closing(connection.cursor()) as cursor:
                params = self.relationship_query_params()
                if params is None:
                    cursor.execute(self.relationship_sql)
                else:
                    cursor.execute(self.relationship_sql, params)
                rows = cursor.fetchall()
            visible = {str(table).lower(): {str(column).lower() for column in columns} for table, columns in tables.items()}
            grouped = {}
            for row in rows:
                if len(row) < 4:
                    continue
                from_table, from_column, to_table, to_column = (str(value) for value in row[:4])
                from_key, to_key = from_table.lower(), to_table.lower()
                if from_key not in visible or to_key not in visible:
                    continue
                if from_column.lower() not in visible[from_key] or to_column.lower() not in visible[to_key]:
                    continue
                relation_name = str(row[4]) if len(row) > 4 and row[4] is not None else ""
                position = int(row[5]) if len(row) > 5 and row[5] is not None else len(grouped)
                key = (from_key, to_key, relation_name)
                grouped.setdefault(key, []).append((position, from_column, to_column))
            relationships = []
            for (from_key, to_key, _), columns in grouped.items():
                columns.sort(key=lambda item: item[0])
                relationships.append(
                    GatewayRelationship(
                        from_table=next(name for name in tables if name.lower() == from_key),
                        from_columns=[item[1] for item in columns],
                        to_table=next(name for name in tables if name.lower() == to_key),
                        to_columns=[item[2] for item in columns],
                        cardinality="many_to_one",
                        confidence=1.0,
                        source="database_metadata",
                    )
                )
            return GatewaySchemaDetails(tables, relationships)
        except GatewayError:
            raise
        except Exception as exc:
            raise GatewayError("E_GATEWAY_SCHEMA", "Connector relationships unavailable", operation="schema_details") from exc

    def schema_query_params(self):
        return None

    def relationship_query_params(self):
        return None

    def estimate_cost(self, request):
        """Return a stable unsupported result until vendor EXPLAIN is verified."""
        return GatewayCostEstimate(
            self.backend,
            False,
            reason="vendor cost estimation is not implemented for this experimental connector",
        )

    def preview(self, table, limit=20, offset=0):
        if table not in self.schema():
            raise GatewayError("E_GATEWAY_TABLE", "Table is not available", operation="preview")
        sql = exp.select("*").from_(exp.Table(this=exp.to_identifier(table, quoted=True))).sql(dialect="sqlite")
        return self.query(GatewayQuery(sql, max_rows=limit, offset=offset))

    def compile_query(self, request):
        if request.max_rows > 10000 or request.offset > 100000 or (request.timeout_seconds is not None and request.timeout_seconds <= 0):
            raise GatewayError("E_GATEWAY_LIMIT", "Connector pagination limit exceeded", operation="query")
        tree = select_tree(request.sql, "sqlite")
        sql_offset = tree.args.get("offset")
        if sql_offset and (
            not isinstance(sql_offset.expression, exp.Literal)
            or not sql_offset.expression.is_int
            or not 0 <= int(sql_offset.expression.this) + request.offset <= 100000
        ):
            raise QueryError("E_QUERY_LIMIT", "SQL offset exceeds the connector limit")
        validate_sql(request.sql, {table: set(columns) for table, columns in self.schema().items()})
        bound = request.offset + request.max_rows + 1
        limit = tree.args.get("limit")
        if limit:
            value = limit.expression
            if not isinstance(value, exp.Literal) or not value.is_int or int(value.this) < 0 or limit.args.get("limit_options"):
                raise QueryError("E_QUERY_LIMIT", "Only a non-negative literal LIMIT is supported")
            bound = min(bound, int(value.this))
        tree = tree.limit(bound)
        params = {node.this: request.params[node.this] for node in tree.find_all(exp.Placeholder) if node.this in request.params}
        return compile_remote_sql(tree, self.backend, params), params

    def query(self, request):
        try:
            sql, params = self.compile_query(request)
            with closing(self.connect()) as connection, closing(connection.cursor()) as cursor:
                self.prepare_query(connection, cursor, request.timeout_seconds)
                self.execute_query(cursor, sql, params)
                columns = [item[0] for item in cursor.description or []]
                remaining = request.offset
                while remaining:
                    skipped = cursor.fetchmany(min(remaining, 1000))
                    if not skipped:
                        break
                    remaining -= len(skipped)
                rows = cursor.fetchmany(request.max_rows + 1)
            result = [list(row) for row in rows[:request.max_rows]]
            return GatewayResult(columns, result, len(result), len(rows) > request.max_rows, request.operation_id)
        except GatewayError:
            raise
        except QueryError as exc:
            raise GatewayError(exc.code, str(exc), operation="query") from exc
        except Exception as exc:
            raise GatewayError("E_GATEWAY_QUERY", "Connector query failed", operation="query") from exc

    def prepare_query(self, connection, cursor, timeout):
        pass

    def execute_query(self, cursor, sql, params):
        cursor.execute(sql, params)

    def cancel(self, operation_id):
        return False
