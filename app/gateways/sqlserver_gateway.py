from app.config import settings
from app.domain.data_gateway import GatewayCapabilities
from app.gateways.remote_gateway import RemoteGateway


class SQLServerGateway(RemoteGateway):
    backend = "sqlserver"
    capabilities = GatewayCapabilities(cancel=False, cost_estimate=False, relationships=True)
    schema_sql = "SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = SCHEMA_NAME() ORDER BY TABLE_NAME, ORDINAL_POSITION"
    relationship_sql = """
        SELECT child_table.name, child_column.name, parent_table.name,
               parent_column.name, foreign_key.name, foreign_key_column.constraint_column_id
        FROM sys.foreign_key_columns foreign_key_column
        JOIN sys.foreign_keys foreign_key
          ON foreign_key.object_id = foreign_key_column.constraint_object_id
        JOIN sys.tables child_table ON child_table.object_id = foreign_key_column.parent_object_id
        JOIN sys.columns child_column
          ON child_column.object_id = foreign_key_column.parent_object_id
         AND child_column.column_id = foreign_key_column.parent_column_id
        JOIN sys.tables parent_table ON parent_table.object_id = foreign_key_column.referenced_object_id
        JOIN sys.columns parent_column
          ON parent_column.object_id = foreign_key_column.referenced_object_id
         AND parent_column.column_id = foreign_key_column.referenced_column_id
        WHERE SCHEMA_NAME(child_table.schema_id) = SCHEMA_NAME()
          AND SCHEMA_NAME(parent_table.schema_id) = SCHEMA_NAME()
        ORDER BY foreign_key.name, foreign_key_column.constraint_column_id
    """

    @staticmethod
    def default_connect():
        import pymssql
        if not settings.sqlserver_host or not settings.sqlserver_user or not settings.sqlserver_password or not settings.sqlserver_database:
            raise RuntimeError("SQL Server configuration missing")
        return pymssql.connect(
            server=settings.sqlserver_host, port=str(settings.sqlserver_port),
            user=settings.sqlserver_user, password=settings.sqlserver_password,
            database=settings.sqlserver_database, charset="UTF-8",
            login_timeout=int(settings.gateway_connect_timeout_seconds),
            timeout=int(settings.gateway_query_timeout_seconds), autocommit=False, read_only=True,
        )

    def prepare_query(self, connection, cursor, timeout):
        cursor.execute("SET QUOTED_IDENTIFIER ON")
        cursor.execute("SET LOCK_TIMEOUT 5000")
