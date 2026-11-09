from app.config import settings
from app.gateways.remote_gateway import RemoteGateway


class ClickHouseGateway(RemoteGateway):
    backend = "clickhouse"
    schema_sql = "SELECT table, name FROM system.columns WHERE database = currentDatabase() ORDER BY table, position"

    @staticmethod
    def default_connect():
        from clickhouse_driver import dbapi
        if not settings.clickhouse_host or not settings.clickhouse_user or not settings.clickhouse_password or not settings.clickhouse_database:
            raise RuntimeError("ClickHouse configuration missing")
        # ClickHouse rejects any session setting change while `readonly=1` is
        # active, including `max_execution_time`, so the two cannot be sent
        # together. Read-only is therefore enforced twice without that flag:
        # the AST guardrails only allow single SELECT statements, and the
        # acceptance account is granted SELECT only, so a write is rejected by
        # the server (error 164/497) rather than by a client-side setting.
        return dbapi.connect(
            host=settings.clickhouse_host, port=settings.clickhouse_port,
            user=settings.clickhouse_user, password=settings.clickhouse_password,
            database=settings.clickhouse_database, secure=settings.clickhouse_secure,
            connect_timeout=settings.gateway_connect_timeout_seconds,
            send_receive_timeout=settings.gateway_query_timeout_seconds,
            settings={"max_execution_time": int(settings.gateway_query_timeout_seconds)},
        )
