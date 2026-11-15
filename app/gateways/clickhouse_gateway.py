from contextlib import closing

from app.config import settings
from app.domain.data_gateway import GatewayCapabilities, GatewayCostEstimate, GatewayError
from app.guardrails import QueryError
from app.gateways.remote_gateway import RemoteGateway


class ClickHouseGateway(RemoteGateway):
    backend = "clickhouse"
    capabilities = GatewayCapabilities(cancel=True, cost_estimate=True, relationships=False)
    schema_sql = "SELECT table, name FROM system.columns WHERE database = currentDatabase() ORDER BY table, position"
    multi_schema_sql = """
        SELECT database, table, name
        FROM system.columns
        WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
        ORDER BY database, table, position
    """
    deep_schema_sql = """
        SELECT columns.table, columns.name, columns.type,
               startsWith(columns.type, 'Nullable('), columns.comment,
               columns.position, columns.is_in_primary_key,
               columns.is_in_sorting_key, tables.comment
        FROM system.columns columns
        LEFT JOIN system.tables tables
          ON tables.database = columns.database AND tables.name = columns.table
        WHERE columns.database = currentDatabase()
        ORDER BY columns.table, columns.position
    """
    multi_deep_schema_sql = """
        SELECT columns.database, columns.table, columns.name, columns.type,
               startsWith(columns.type, 'Nullable('), columns.comment,
               columns.position, columns.is_in_primary_key,
               columns.is_in_sorting_key, tables.comment
        FROM system.columns columns
        LEFT JOIN system.tables tables
          ON tables.database = columns.database AND tables.name = columns.table
        WHERE columns.database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
        ORDER BY columns.database, columns.table, columns.position
    """
    multi_relationship_sql = ""

    @staticmethod
    def default_connect():
        from clickhouse_driver import dbapi
        if not settings.clickhouse_host or not settings.clickhouse_user or not settings.clickhouse_password or not settings.clickhouse_database:
            raise RuntimeError("ClickHouse configuration missing")
        if settings.clickhouse_tls_required and not settings.clickhouse_secure:
            raise RuntimeError("ClickHouse TLS configuration missing")
        # ClickHouse rejects any session setting change while `readonly=1` is
        # active, including `max_execution_time`, so the two cannot be sent
        # together. Read-only is therefore enforced twice without that flag:
        # the AST guardrails only allow single SELECT statements, and the
        # acceptance account is granted SELECT only, so a write is rejected by
        # the server (error 164/497) rather than by a client-side setting.
        tls_options = {}
        if settings.clickhouse_secure:
            tls_options["verify"] = settings.clickhouse_tls_verify
            if settings.clickhouse_ca_certs.strip():
                tls_options["ca_certs"] = settings.clickhouse_ca_certs.strip()
        return dbapi.connect(
            host=settings.clickhouse_host, port=settings.clickhouse_port,
            user=settings.clickhouse_user, password=settings.clickhouse_password,
            database=settings.clickhouse_database, secure=settings.clickhouse_secure,
            connect_timeout=settings.gateway_connect_timeout_seconds,
            send_receive_timeout=settings.gateway_query_timeout_seconds,
            settings={
                "max_execution_time": int(settings.gateway_query_timeout_seconds),
                "max_memory_usage": settings.clickhouse_max_memory_usage,
                "max_rows_to_read": settings.clickhouse_max_rows_to_read,
                "max_bytes_to_read": settings.clickhouse_max_bytes_to_read,
                "max_threads": settings.clickhouse_max_threads,
            },
            **tls_options,
        )

    def estimate_cost(self, request):
        try:
            sql, params = self.compile_query(request)
            with self._connection() as connection, closing(connection.cursor()) as cursor:
                cursor.execute("EXPLAIN ESTIMATE " + sql, params)
                rows = cursor.fetchall()
            if not rows:
                raise GatewayError("E_GATEWAY_COST", "ClickHouse EXPLAIN ESTIMATE returned no rows", operation="estimate_cost")
            tables = []
            total_parts = total_rows = total_marks = 0
            for row in rows[:200]:
                if len(row) < 5:
                    raise GatewayError("E_GATEWAY_COST", "ClickHouse EXPLAIN ESTIMATE returned an invalid row", operation="estimate_cost")
                parts, estimated_rows, marks = (max(0, int(row[index] or 0)) for index in (2, 3, 4))
                total_parts += parts
                total_rows += estimated_rows
                total_marks += marks
                tables.append({
                    "database": str(row[0] or "")[:256],
                    "table": str(row[1] or "")[:256],
                    "parts": parts,
                    "rows": estimated_rows,
                    "marks": marks,
                })
            return GatewayCostEstimate(
                "clickhouse",
                True,
                estimated_rows=total_rows,
                estimated_cost=float(total_marks),
                full_scan=None,
                index_used=None,
                reason="ClickHouse EXPLAIN ESTIMATE scan marks",
                plan={"parts": total_parts, "rows": total_rows, "marks": total_marks, "tables": tables},
            )
        except GatewayError:
            raise
        except QueryError as exc:
            raise GatewayError(exc.code, str(exc), operation="estimate_cost") from exc
        except Exception as exc:
            raise GatewayError("E_GATEWAY_COST", "ClickHouse cost estimation failed", operation="estimate_cost", retryable=True) from exc

    def cancel_query(self, connection, cursor, operation_id):
        client = getattr(cursor, "_client", None)
        cancel = getattr(client, "cancel", None)
        if not callable(cancel):
            return super().cancel_query(connection, cursor, operation_id)
        cancel()
        return True
