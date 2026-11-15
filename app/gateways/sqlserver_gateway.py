from contextlib import closing
from xml.etree import ElementTree

from app.config import settings
from app.domain.data_gateway import GatewayCapabilities, GatewayCostEstimate, GatewayError
from app.guardrails import QueryError
from app.gateways.remote_gateway import RemoteGateway


class SQLServerGateway(RemoteGateway):
    backend = "sqlserver"
    capabilities = GatewayCapabilities(cancel=True, cost_estimate=False, relationships=True)
    schema_sql = "SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = SCHEMA_NAME() ORDER BY TABLE_NAME, ORDINAL_POSITION"
    multi_schema_sql = "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION"
    deep_schema_sql = """
        SELECT tables.name, columns.name, types.name, columns.is_nullable,
               column_comments.value, columns.column_id,
               COALESCE(indexes.is_primary, 0), COALESCE(indexes.is_indexed, 0),
               table_comments.value
        FROM sys.tables tables
        JOIN sys.schemas schemas ON schemas.schema_id = tables.schema_id
        JOIN sys.columns columns ON columns.object_id = tables.object_id
        JOIN sys.types types ON types.user_type_id = columns.user_type_id
        LEFT JOIN sys.extended_properties column_comments
          ON column_comments.major_id = tables.object_id
         AND column_comments.minor_id = columns.column_id
         AND column_comments.name = 'MS_Description'
        LEFT JOIN sys.extended_properties table_comments
          ON table_comments.major_id = tables.object_id
         AND table_comments.minor_id = 0
         AND table_comments.name = 'MS_Description'
        LEFT JOIN (
            SELECT index_columns.object_id, index_columns.column_id,
                   MAX(CASE WHEN indexes.is_primary_key = 1 THEN 1 ELSE 0 END) AS is_primary,
                   1 AS is_indexed
            FROM sys.index_columns index_columns
            JOIN sys.indexes indexes
              ON indexes.object_id = index_columns.object_id
             AND indexes.index_id = index_columns.index_id
            WHERE indexes.is_hypothetical = 0
            GROUP BY index_columns.object_id, index_columns.column_id
        ) indexes
          ON indexes.object_id = columns.object_id
         AND indexes.column_id = columns.column_id
        WHERE schemas.name = SCHEMA_NAME()
        ORDER BY tables.name, columns.column_id
    """
    multi_deep_schema_sql = """
        SELECT schemas.name, tables.name, columns.name, types.name, columns.is_nullable,
               column_comments.value, columns.column_id,
               COALESCE(indexes.is_primary, 0), COALESCE(indexes.is_indexed, 0),
               table_comments.value
        FROM sys.tables tables
        JOIN sys.schemas schemas ON schemas.schema_id = tables.schema_id
        JOIN sys.columns columns ON columns.object_id = tables.object_id
        JOIN sys.types types ON types.user_type_id = columns.user_type_id
        LEFT JOIN sys.extended_properties column_comments
          ON column_comments.major_id = tables.object_id
         AND column_comments.minor_id = columns.column_id
         AND column_comments.name = 'MS_Description'
        LEFT JOIN sys.extended_properties table_comments
          ON table_comments.major_id = tables.object_id
         AND table_comments.minor_id = 0
         AND table_comments.name = 'MS_Description'
        LEFT JOIN (
            SELECT index_columns.object_id, index_columns.column_id,
                   MAX(CASE WHEN indexes.is_primary_key = 1 THEN 1 ELSE 0 END) AS is_primary,
                   1 AS is_indexed
            FROM sys.index_columns index_columns
            JOIN sys.indexes indexes
              ON indexes.object_id = index_columns.object_id
             AND indexes.index_id = index_columns.index_id
            WHERE indexes.is_hypothetical = 0
            GROUP BY index_columns.object_id, index_columns.column_id
        ) indexes
          ON indexes.object_id = columns.object_id
         AND indexes.column_id = columns.column_id
        ORDER BY schemas.name, tables.name, columns.column_id
    """
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
    multi_relationship_sql = """
        SELECT child_schema.name, child_table.name, child_column.name,
               parent_schema.name, parent_table.name, parent_column.name,
               foreign_key.name, foreign_key_column.constraint_column_id
        FROM sys.foreign_key_columns foreign_key_column
        JOIN sys.foreign_keys foreign_key
          ON foreign_key.object_id = foreign_key_column.constraint_object_id
        JOIN sys.tables child_table ON child_table.object_id = foreign_key_column.parent_object_id
        JOIN sys.schemas child_schema ON child_schema.schema_id = child_table.schema_id
        JOIN sys.columns child_column
          ON child_column.object_id = foreign_key_column.parent_object_id
         AND child_column.column_id = foreign_key_column.parent_column_id
        JOIN sys.tables parent_table ON parent_table.object_id = foreign_key_column.referenced_object_id
        JOIN sys.schemas parent_schema ON parent_schema.schema_id = parent_table.schema_id
        JOIN sys.columns parent_column
          ON parent_column.object_id = foreign_key_column.referenced_object_id
         AND parent_column.column_id = foreign_key_column.referenced_column_id
        ORDER BY child_schema.name, foreign_key.name, foreign_key_column.constraint_column_id
    """

    def __init__(
        self,
        *,
        gateway_id,
        name,
        allowed_tables=None,
        connect=None,
        plan_connect=None,
        cost_estimate_enabled=False,
        integration_status="implemented",
        production_status="not_validated",
        capability_gaps=None,
        namespaces=None,
    ):
        if not isinstance(cost_estimate_enabled, bool):
            raise GatewayError("E_GATEWAY_CONFIG", "SQL Server cost_estimate_enabled must be boolean", operation="configure")
        plan_user_configured = bool(settings.sqlserver_plan_user.strip())
        plan_password_configured = bool(settings.sqlserver_plan_password.strip())
        if plan_user_configured != plan_password_configured:
            raise GatewayError("E_GATEWAY_CONFIG", "SQL Server planner credentials are incomplete", operation="configure")
        if plan_connect is not None:
            self.plan_connect = plan_connect
        elif cost_estimate_enabled:
            self.plan_connect = self.default_plan_connect
        else:
            self.plan_connect = None
        self.capabilities = GatewayCapabilities(
            cancel=True,
            cost_estimate=self.plan_connect is not None,
            relationships=True,
        )
        super().__init__(
            gateway_id=gateway_id,
            name=name,
            allowed_tables=allowed_tables,
            connect=connect,
            integration_status=integration_status,
            production_status=production_status,
            capability_gaps=capability_gaps,
            namespaces=namespaces,
        )

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
            encryption="require" if settings.sqlserver_tls_required else None,
        )

    @staticmethod
    def default_plan_connect():
        import pymssql
        user = settings.sqlserver_plan_user.strip() or settings.sqlserver_user
        password = settings.sqlserver_plan_password or settings.sqlserver_password
        if not settings.sqlserver_host or not user or not password or not settings.sqlserver_database:
            raise RuntimeError("SQL Server planner configuration missing")
        return pymssql.connect(
            server=settings.sqlserver_host,
            port=str(settings.sqlserver_port),
            user=user,
            password=password,
            database=settings.sqlserver_database,
            charset="UTF-8",
            login_timeout=int(settings.gateway_connect_timeout_seconds),
            timeout=int(settings.gateway_query_timeout_seconds),
            autocommit=False,
            read_only=True,
            encryption="require" if settings.sqlserver_tls_required else None,
        )

    def prepare_query(self, connection, cursor, timeout):
        cursor.execute("SET QUOTED_IDENTIFIER ON")
        cursor.execute("SET LOCK_TIMEOUT 5000")

    def cancel_query(self, connection, cursor, operation_id):
        low_level = getattr(connection, "_conn", None)
        cancel = getattr(low_level, "cancel", None)
        if not callable(cancel):
            return super().cancel_query(connection, cursor, operation_id)
        cancel()
        return True

    @staticmethod
    def _clean_identifier(value):
        return str(value or "").strip("[]\"")[:256]

    @classmethod
    def _parse_showplan(cls, payload):
        if isinstance(payload, bytes):
            encoding = "utf-16" if payload.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
            payload = payload.decode(encoding)
        if not isinstance(payload, str) or not payload.strip():
            raise GatewayError("E_GATEWAY_COST", "SQL Server SHOWPLAN returned no XML", operation="estimate_cost")
        root = ElementTree.fromstring(payload)

        def local_name(element):
            return element.tag.rsplit("}", 1)[-1]

        statement = next((element for element in root.iter() if local_name(element) == "StmtSimple"), None)
        operations = []
        for element in root.iter():
            if local_name(element) != "RelOp" or len(operations) >= 200:
                continue
            obj = next((child for child in element.iter() if local_name(child) == "Object"), None)
            physical = str(element.attrib.get("PhysicalOp", ""))[:120]
            operations.append({
                "node_id": int(element.attrib.get("NodeId", len(operations))),
                "physical_op": physical,
                "logical_op": str(element.attrib.get("LogicalOp", ""))[:120],
                "estimated_rows": float(element.attrib["EstimateRows"]) if element.attrib.get("EstimateRows") else None,
                "estimated_cost": float(element.attrib["EstimatedTotalSubtreeCost"]) if element.attrib.get("EstimatedTotalSubtreeCost") else None,
                "database": cls._clean_identifier(obj.attrib.get("Database")) if obj is not None else "",
                "schema": cls._clean_identifier(obj.attrib.get("Schema")) if obj is not None else "",
                "table": cls._clean_identifier(obj.attrib.get("Table")) if obj is not None else "",
                "index": cls._clean_identifier(obj.attrib.get("Index")) if obj is not None else "",
            })
        if not operations:
            raise GatewayError("E_GATEWAY_COST", "SQL Server SHOWPLAN returned no operators", operation="estimate_cost")
        estimated_rows = None
        estimated_cost = None
        if statement is not None:
            if statement.attrib.get("StatementEstRows"):
                estimated_rows = int(float(statement.attrib["StatementEstRows"]))
            if statement.attrib.get("StatementSubTreeCost"):
                estimated_cost = float(statement.attrib["StatementSubTreeCost"])
        root_operation = operations[0]
        if estimated_rows is None and root_operation["estimated_rows"] is not None:
            estimated_rows = int(root_operation["estimated_rows"])
        if estimated_cost is None:
            estimated_cost = root_operation["estimated_cost"]
        physical_ops = [item["physical_op"].lower() for item in operations]
        full_scan = any("table scan" in item or "clustered index scan" in item for item in physical_ops)
        index_used = any("index" in item for item in physical_ops)
        return GatewayCostEstimate(
            "sqlserver",
            True,
            estimated_rows=estimated_rows,
            estimated_cost=estimated_cost,
            full_scan=full_scan,
            index_used=index_used,
            reason="SQL Server SHOWPLAN_XML using a read-only planner identity",
            plan={"operations": operations},
        )

    def estimate_cost(self, request):
        if self.plan_connect is None:
            return super().estimate_cost(request)
        try:
            sql, params = self.compile_query(request)
            with closing(self.plan_connect()) as connection, closing(connection.cursor()) as cursor:
                enabled = False
                try:
                    cursor.execute("SET SHOWPLAN_XML ON")
                    enabled = True
                    cursor.execute(sql, params)
                    rows = cursor.fetchall()
                finally:
                    if enabled:
                        cursor.execute("SET SHOWPLAN_XML OFF")
            if not rows or not rows[0]:
                raise GatewayError("E_GATEWAY_COST", "SQL Server SHOWPLAN returned no rows", operation="estimate_cost")
            return self._parse_showplan(rows[0][0])
        except GatewayError:
            raise
        except QueryError as exc:
            raise GatewayError(exc.code, str(exc), operation="estimate_cost") from exc
        except Exception as exc:
            raise GatewayError("E_GATEWAY_COST", "SQL Server cost estimation failed", operation="estimate_cost", retryable=True) from exc
