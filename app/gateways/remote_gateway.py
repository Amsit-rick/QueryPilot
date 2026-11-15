"""Shared bounded DB-API operations for experimental remote gateways."""
from contextlib import closing, contextmanager
import re
from threading import RLock

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
from app.config import settings
from app.gateways.connection_pool import ConnectionPoolExhausted, shared_remote_pool
from app.gateways.errors import classify_gateway_error
from app.gateways.remote_sql import REMOTE_DIALECTS, compile_remote_sql, select_tree


class RemoteGateway:
    capabilities = GatewayCapabilities(cancel=False)
    backend = ""
    health_sql = "SELECT 1"
    schema_sql = ""
    deep_schema_sql = ""
    relationship_sql = ""
    multi_schema_sql = ""
    multi_deep_schema_sql = ""
    multi_relationship_sql = ""

    _integration_statuses = frozenset({"implemented", "accepted_local"})
    _production_statuses = frozenset({"not_validated"})
    _capability_gap_names = frozenset({
        "relationships",
        "cost_estimate",
        "deep_schema",
        "multi_schema",
        "multi_database",
        "tls_validation",
    })

    def __init__(
        self,
        *,
        gateway_id,
        name,
        allowed_tables=None,
        connect=None,
        integration_status="implemented",
        production_status="not_validated",
        capability_gaps=None,
        namespaces=None,
    ):
        if allowed_tables is not None and (
            not isinstance(allowed_tables, list)
            or any(not isinstance(table, str) or not table.strip() for table in allowed_tables)
        ):
            raise GatewayError("E_GATEWAY_CONFIG", "allowed_tables must be a list of table names", operation="configure")
        if integration_status not in self._integration_statuses:
            raise GatewayError("E_GATEWAY_CONFIG", "Invalid connector integration status", operation="configure")
        if production_status not in self._production_statuses:
            raise GatewayError("E_GATEWAY_CONFIG", "Invalid connector production status", operation="configure")
        if capability_gaps is None:
            capability_gaps = []
        if (
            not isinstance(capability_gaps, list)
            or any(not isinstance(item, str) or item not in self._capability_gap_names for item in capability_gaps)
        ):
            raise GatewayError("E_GATEWAY_CONFIG", "Invalid connector capability gaps", operation="configure")
        normalized_gaps = list(dict.fromkeys(capability_gaps))
        if namespaces is None:
            namespaces = []
        if (
            not isinstance(namespaces, list)
            or any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$#]{0,127}", value.strip()) for value in namespaces)
        ):
            raise GatewayError("E_GATEWAY_CONFIG", "namespaces must contain safe identifiers", operation="configure")
        normalized_namespaces = list(dict.fromkeys(value.strip() for value in namespaces))
        if normalized_namespaces and allowed_tables is None:
            raise GatewayError("E_GATEWAY_CONFIG", "namespaces require an explicit table allowlist", operation="configure")
        normalized_allowed = list(allowed_tables if allowed_tables is not None else ["sales", "customers"])
        if len(normalized_namespaces) == 1:
            namespace = normalized_namespaces[0]
            normalized_allowed = [value if "." in value else f"{namespace}.{value}" for value in normalized_allowed]
        if len(normalized_namespaces) > 1 and any("." not in value for value in normalized_allowed):
            raise GatewayError("E_GATEWAY_CONFIG", "multiple namespaces require qualified allowed_tables", operation="configure")
        namespace_keys = {value.lower() for value in normalized_namespaces}
        if normalized_namespaces and any(value.split(".", 1)[0].lower() not in namespace_keys for value in normalized_allowed):
            raise GatewayError("E_GATEWAY_CONFIG", "allowed_tables contains an unconfigured namespace", operation="configure")
        if normalized_namespaces and not self.multi_schema_sql:
            raise GatewayError("E_GATEWAY_CONFIG", "connector does not support multiple namespaces", operation="configure")
        self.namespaces = tuple(normalized_namespaces)
        if self.namespaces:
            self.schema_sql = self.multi_schema_sql
            self.deep_schema_sql = self.multi_deep_schema_sql
            self.relationship_sql = self.multi_relationship_sql
        self.allowed = {name.lower() for name in normalized_allowed}
        self.connect = connect or self.default_connect
        self._connection_pool = None
        if connect is None:
            self._connection_pool = shared_remote_pool(
                self.backend,
                self.connect,
                size=settings.gateway_max_connections,
                timeout=settings.gateway_pool_acquire_timeout_seconds,
                idle_ttl=settings.gateway_pool_idle_ttl_seconds,
            )
        self._operation_lock = RLock()
        self._active_operations = {}
        self.descriptor = GatewayDescriptor(
            gateway_id, name, self.backend, REMOTE_DIALECTS[self.backend],
            metadata={
                "integration_status": integration_status,
                "production_status": production_status,
                "capability_gaps": normalized_gaps,
            },
        )

    def test_connection(self):
        try:
            with self._connection() as connection, closing(connection.cursor()) as cursor:
                cursor.execute(self.health_sql)
                cursor.fetchone()
            return GatewayHealth("healthy", "Connection succeeded; compatibility acceptance is separate")
        except Exception:
            return GatewayHealth("unavailable", "Connector unavailable; check optional driver and local configuration")

    health = test_connection

    def schema(self):
        try:
            with self._connection() as connection, closing(connection.cursor()) as cursor:
                params = self.schema_query_params()
                if params is None:
                    cursor.execute(self.schema_sql)
                else:
                    cursor.execute(self.schema_sql, params)
                result = {}
                for row in cursor.fetchall():
                    if self.namespaces:
                        if len(row) < 3:
                            continue
                        namespace, table, column = (str(value) for value in row[:3])
                        if namespace.lower() not in {value.lower() for value in self.namespaces}:
                            continue
                        table = f"{namespace}.{table}"
                    else:
                        if len(row) < 2:
                            continue
                        table, column = (str(value) for value in row[:2])
                    if table.lower() in self.allowed:
                        result.setdefault(table, []).append(column)
                return result
        except Exception as exc:
            raise classify_gateway_error(exc, operation="schema", fallback_code="E_GATEWAY_SCHEMA") from exc

    def schema_details(self):
        """Return typed column metadata plus database-declared foreign keys."""
        simple_tables = self.schema()
        tables = self._deep_schema_tables(simple_tables)
        if not self.relationship_sql:
            return GatewaySchemaDetails(tables, [])
        try:
            with self._connection() as connection, closing(connection.cursor()) as cursor:
                params = self.relationship_query_params()
                if params is None:
                    cursor.execute(self.relationship_sql)
                else:
                    cursor.execute(self.relationship_sql, params)
                rows = cursor.fetchall()
            visible = {
                str(table).lower(): {str(column).lower() for column in columns}
                for table, columns in simple_tables.items()
            }
            grouped = {}
            for row in rows:
                if self.namespaces:
                    if len(row) < 8:
                        continue
                    from_namespace, from_table, from_column, to_namespace, to_table, to_column = (
                        str(value) for value in row[:6]
                    )
                    from_table = f"{from_namespace}.{from_table}"
                    to_table = f"{to_namespace}.{to_table}"
                    relation_name = str(row[6]) if row[6] is not None else ""
                    position = int(row[7]) if row[7] is not None else len(grouped)
                else:
                    if len(row) < 4:
                        continue
                    from_table, from_column, to_table, to_column = (str(value) for value in row[:4])
                    relation_name = str(row[4]) if len(row) > 4 and row[4] is not None else ""
                    position = int(row[5]) if len(row) > 5 and row[5] is not None else len(grouped)
                from_key, to_key = from_table.lower(), to_table.lower()
                if from_key not in visible or to_key not in visible:
                    continue
                if from_column.lower() not in visible[from_key] or to_column.lower() not in visible[to_key]:
                    continue
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
            raise classify_gateway_error(exc, operation="schema_details", fallback_code="E_GATEWAY_SCHEMA") from exc

    @staticmethod
    def _metadata_bool(value) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y"}
        return bool(value)

    def _deep_schema_tables(self, simple_tables):
        tables = {
            table: {
                "columns": list(columns),
                "primary_key": [],
                "indexed_columns": [],
                "column_details": {},
            }
            for table, columns in simple_tables.items()
        }
        if not self.deep_schema_sql:
            return tables
        canonical_tables = {str(table).lower(): table for table in simple_tables}
        canonical_columns = {
            str(table).lower(): {str(column).lower(): column for column in columns}
            for table, columns in simple_tables.items()
        }
        with self._connection() as connection, closing(connection.cursor()) as cursor:
            params = self.schema_details_query_params()
            if params is None:
                cursor.execute(self.deep_schema_sql)
            else:
                cursor.execute(self.deep_schema_sql, params)
            rows = cursor.fetchall()
        for row in rows:
            if self.namespaces:
                if len(row) < 10:
                    continue
                table_key = f"{row[0]}.{row[1]}".lower()
                column_key = str(row[2]).lower()
                values = row[3:]
            else:
                if len(row) < 9:
                    continue
                table_key = str(row[0]).lower()
                column_key = str(row[1]).lower()
                values = row[2:]
            table = canonical_tables.get(table_key)
            column = canonical_columns.get(table_key, {}).get(column_key)
            if table is None or column is None:
                continue
            item = tables[table]
            try:
                ordinal = max(0, int(values[3] or 0))
            except (TypeError, ValueError):
                ordinal = 0
            item["column_details"][column] = {
                "data_type": str(values[0] or "")[:256],
                "nullable": self._metadata_bool(values[1]),
                "comment": str(values[2] or "")[:1000],
                "ordinal": ordinal,
            }
            if self._metadata_bool(values[4]) and column not in item["primary_key"]:
                item["primary_key"].append(column)
            if self._metadata_bool(values[5]) and column not in item["indexed_columns"]:
                item["indexed_columns"].append(column)
            table_comment = str(values[6] or "")[:1000]
            if table_comment:
                item["comment"] = table_comment
        return tables

    def schema_query_params(self):
        return None

    def schema_details_query_params(self):
        return self.schema_query_params()

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
        if "." in table:
            namespace, table_name = table.split(".", 1)
            expression = exp.Table(
                this=exp.to_identifier(table_name, quoted=True),
                db=exp.to_identifier(namespace, quoted=True),
            )
        else:
            expression = exp.Table(this=exp.to_identifier(table, quoted=True))
        sql = exp.select("*").from_(expression).sql(dialect=REMOTE_DIALECTS[self.backend])
        return self.query(GatewayQuery(sql, max_rows=limit, offset=offset))

    def compile_query(self, request):
        if request.max_rows > 10000 or request.offset > 100000 or (request.timeout_seconds is not None and request.timeout_seconds <= 0):
            raise GatewayError("E_GATEWAY_LIMIT", "Connector pagination limit exceeded", operation="query")
        dialect = REMOTE_DIALECTS[self.backend]
        # Reject writes and unsafe connector syntax before opening a schema
        # connection. This preserves the security boundary even when the
        # database is unavailable or a malicious request targets a write.
        tree = select_tree(request.sql, dialect)
        validated = validate_sql(
            request.sql,
            {table: set(columns) for table, columns in self.schema().items()},
            dialect=self.backend,
        )
        tree = select_tree(validated, dialect)
        sql_offset = tree.args.get("offset")
        if sql_offset and (
            not isinstance(sql_offset.expression, exp.Literal)
            or not sql_offset.expression.is_int
            or not 0 <= int(sql_offset.expression.this) + request.offset <= 100000
        ):
            raise QueryError("E_QUERY_LIMIT", "SQL offset exceeds the connector limit")
        bound = request.offset + request.max_rows + 1
        limit = tree.args.get("limit")
        if limit:
            value = limit.args.get("count") if isinstance(limit, exp.Fetch) else limit.expression
            options = limit.args.get("limit_options")
            safe_fetch = isinstance(limit, exp.Fetch) and options is not None and not options.args.get("percent") and not options.args.get("with_ties")
            if not isinstance(value, exp.Literal) or not value.is_int or int(value.this) < 0 or (options and not safe_fetch):
                raise QueryError("E_QUERY_LIMIT", "Only a non-negative literal LIMIT is supported")
            bound = min(bound, int(value.this))
        tree = tree.limit(bound)
        params = {node.this: request.params[node.this] for node in tree.find_all(exp.Placeholder) if node.this in request.params}
        return compile_remote_sql(tree, self.backend, params), params

    def query(self, request):
        self._reserve_operation(request.operation_id)
        try:
            sql, params = self.compile_query(request)
            with self._connection() as connection, closing(connection.cursor()) as cursor:
                self._attach_operation(request.operation_id, connection, cursor)
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
            raise classify_gateway_error(exc, operation="query") from exc
        finally:
            self._release_operation(request.operation_id)

    def _reserve_operation(self, operation_id):
        if not operation_id:
            return
        with self._operation_lock:
            if operation_id in self._active_operations:
                raise GatewayError("E_GATEWAY_CONFLICT", "Connector operation id is already active", operation="query")
            self._active_operations[operation_id] = None

    def _attach_operation(self, operation_id, connection, cursor):
        if not operation_id:
            return
        with self._operation_lock:
            if operation_id in self._active_operations:
                self._active_operations[operation_id] = (connection, cursor)

    def _release_operation(self, operation_id):
        if not operation_id:
            return
        with self._operation_lock:
            self._active_operations.pop(operation_id, None)

    def prepare_query(self, connection, cursor, timeout):
        pass

    def execute_query(self, cursor, sql, params):
        cursor.execute(sql, params)

    def cancel(self, operation_id):
        if not operation_id:
            return False
        with self._operation_lock:
            active = self._active_operations.get(operation_id)
        if not active:
            return False
        try:
            return bool(self.cancel_query(active[0], active[1], operation_id))
        except Exception:
            return False

    def cancel_query(self, connection, cursor, operation_id):
        for target in (cursor, connection):
            cancel = getattr(target, "cancel", None)
            if callable(cancel):
                cancel()
                return True
        return False

    @contextmanager
    def _connection(self):
        if self._connection_pool is None:
            with closing(self.connect()) as connection:
                yield connection
            return
        try:
            with self._connection_pool.acquire() as connection:
                yield connection
        except ConnectionPoolExhausted as exc:
            raise GatewayError(
                "E_GATEWAY_POOL",
                "Connector connection pool exhausted",
                operation="connect",
                retryable=True,
            ) from exc

    def close(self):
        if self._connection_pool is not None:
            self._connection_pool.close_all()
