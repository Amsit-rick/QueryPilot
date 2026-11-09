import re
from contextlib import closing
from uuid import uuid4

from app.config import settings
from app.domain.data_gateway import GatewayCapabilities, GatewayCostEstimate, GatewayError
from app.guardrails import QueryError
from app.gateways.remote_gateway import RemoteGateway


class OracleGateway(RemoteGateway):
    backend = "oracle"
    capabilities = GatewayCapabilities(cancel=False, cost_estimate=False, relationships=True)
    health_sql = "SELECT 1 FROM DUAL"
    schema_sql = "SELECT table_name, column_name FROM user_tab_columns ORDER BY table_name, column_id"
    relationship_sql = """
        SELECT child_cols.table_name, child_cols.column_name,
               parent_cols.table_name, parent_cols.column_name,
               constraints.constraint_name, child_cols.position
        FROM user_constraints constraints
        JOIN user_cons_columns child_cols
          ON child_cols.constraint_name = constraints.constraint_name
         AND child_cols.owner = constraints.owner
        JOIN user_constraints parent_constraints
          ON parent_constraints.constraint_name = constraints.r_constraint_name
         AND parent_constraints.owner = constraints.r_owner
        JOIN user_cons_columns parent_cols
          ON parent_cols.constraint_name = parent_constraints.constraint_name
         AND parent_cols.owner = parent_constraints.owner
         AND parent_cols.position = child_cols.position
        WHERE constraints.constraint_type = 'R'
        ORDER BY constraints.constraint_name, child_cols.position
    """
    owner_schema_sql = """
        SELECT table_name, column_name
        FROM all_tab_columns
        WHERE owner = :schema_owner
        ORDER BY table_name, column_id
    """
    owner_relationship_sql = """
        SELECT child_cols.table_name, child_cols.column_name,
               parent_cols.table_name, parent_cols.column_name,
               constraints.constraint_name, child_cols.position
        FROM all_constraints constraints
        JOIN all_cons_columns child_cols
          ON child_cols.constraint_name = constraints.constraint_name
         AND child_cols.owner = constraints.owner
        JOIN all_constraints parent_constraints
          ON parent_constraints.constraint_name = constraints.r_constraint_name
         AND parent_constraints.owner = constraints.r_owner
        JOIN all_cons_columns parent_cols
          ON parent_cols.constraint_name = parent_constraints.constraint_name
         AND parent_cols.owner = parent_constraints.owner
         AND parent_cols.position = child_cols.position
        WHERE constraints.constraint_type = 'R'
          AND constraints.owner = :schema_owner
          AND parent_constraints.owner = :schema_owner
        ORDER BY constraints.constraint_name, child_cols.position
    """

    def __init__(
        self,
        *,
        gateway_id,
        name,
        allowed_tables=None,
        connect=None,
        schema_owner=None,
        plan_connect=None,
        cost_estimate_enabled=False,
    ):
        configured_owner = schema_owner
        if configured_owner is not None and not isinstance(configured_owner, str):
            raise GatewayError("E_GATEWAY_CONFIG", "Oracle schema_owner must be a valid identifier", operation="configure")
        normalized_owner = configured_owner.strip().upper() if configured_owner is not None else ""
        if configured_owner is not None and not normalized_owner:
            raise GatewayError("E_GATEWAY_CONFIG", "Oracle schema_owner must be a valid identifier", operation="configure")
        if normalized_owner and not re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", normalized_owner):
            raise GatewayError("E_GATEWAY_CONFIG", "Oracle schema_owner must be a valid identifier", operation="configure")
        self.schema_owner = normalized_owner or None
        if self.schema_owner:
            self.schema_sql = self.owner_schema_sql
            self.relationship_sql = self.owner_relationship_sql
        if not isinstance(cost_estimate_enabled, bool):
            raise GatewayError("E_GATEWAY_CONFIG", "Oracle cost_estimate_enabled must be boolean", operation="configure")
        plan_user_configured = bool(settings.oracle_plan_user.strip())
        plan_password_configured = bool(settings.oracle_plan_password.strip())
        if plan_connect is None and cost_estimate_enabled and plan_user_configured != plan_password_configured:
            raise GatewayError("E_GATEWAY_CONFIG", "Oracle planner credentials are incomplete", operation="configure")
        if plan_connect is not None:
            self.plan_connect = plan_connect
        elif cost_estimate_enabled and plan_user_configured:
            self.plan_connect = self.default_plan_connect
        elif cost_estimate_enabled:
            raise GatewayError("E_GATEWAY_CONFIG", "Oracle planner credentials are missing", operation="configure")
        else:
            self.plan_connect = None
        self.capabilities = GatewayCapabilities(
            cancel=False,
            cost_estimate=self.plan_connect is not None,
            relationships=True,
        )
        super().__init__(gateway_id=gateway_id, name=name, allowed_tables=allowed_tables, connect=connect)

    def schema_query_params(self):
        return {"schema_owner": self.schema_owner} if self.schema_owner else None

    def relationship_query_params(self):
        return {"schema_owner": self.schema_owner} if self.schema_owner else None

    @staticmethod
    def default_connect():
        import oracledb
        if not settings.oracle_dsn or not settings.oracle_user or not settings.oracle_password:
            raise RuntimeError("Oracle configuration missing")
        connection = oracledb.connect(
            user=settings.oracle_user, password=settings.oracle_password,
            dsn=settings.oracle_dsn, tcp_connect_timeout=settings.gateway_connect_timeout_seconds,
        )
        connection.call_timeout = int(settings.gateway_query_timeout_seconds * 1000)
        return connection

    @staticmethod
    def default_plan_connect():
        import oracledb
        if not settings.oracle_dsn or not settings.oracle_plan_user or not settings.oracle_plan_password:
            raise RuntimeError("Oracle planner configuration missing")
        connection = oracledb.connect(
            user=settings.oracle_plan_user,
            password=settings.oracle_plan_password,
            dsn=settings.oracle_dsn,
            tcp_connect_timeout=settings.gateway_connect_timeout_seconds,
        )
        connection.call_timeout = int(settings.gateway_query_timeout_seconds * 1000)
        return connection

    def prepare_query(self, connection, cursor, timeout):
        connection.call_timeout = int(min(timeout or settings.gateway_query_timeout_seconds, settings.gateway_query_timeout_seconds) * 1000)
        if self.schema_owner:
            cursor.execute(f'ALTER SESSION SET CURRENT_SCHEMA = "{self.schema_owner}"')
        cursor.execute("SET TRANSACTION READ ONLY")

    def execute_query(self, cursor, sql, params):
        cursor.execute(sql, params, fetch_decimals=True)

    def estimate_cost(self, request):
        if self.plan_connect is None:
            return super().estimate_cost(request)
        try:
            sql, params = self.compile_query(request)
            statement_id = f"QP{uuid4().hex[:24]}".upper()
            statement_params = {"statement_id": statement_id}
            rows = []
            with closing(self.plan_connect()) as connection, closing(connection.cursor()) as cursor:
                connection.call_timeout = int(
                    min(
                        request.timeout_seconds or settings.gateway_query_timeout_seconds,
                        settings.gateway_query_timeout_seconds,
                    )
                    * 1000
                )
                if self.schema_owner:
                    cursor.execute(f'ALTER SESSION SET CURRENT_SCHEMA = "{self.schema_owner}"')
                cursor.execute("DELETE FROM plan_table WHERE statement_id=:statement_id", statement_params)
                try:
                    cursor.execute(
                        f"EXPLAIN PLAN SET STATEMENT_ID = '{statement_id}' FOR {sql}",
                        params,
                    )
                    cursor.execute(
                        """
                        SELECT id, operation, options, object_name, cardinality, cost
                        FROM plan_table
                        WHERE statement_id=:statement_id
                        ORDER BY id
                        """,
                        statement_params,
                    )
                    rows = cursor.fetchall()
                finally:
                    cursor.execute("DELETE FROM plan_table WHERE statement_id=:statement_id", statement_params)
                    connection.commit()
            if not rows:
                raise GatewayError("E_GATEWAY_COST", "Oracle EXPLAIN returned no plan rows", operation="estimate_cost")
            operations = [
                {
                    "id": int(row[0]),
                    "operation": str(row[1] or ""),
                    "options": str(row[2] or ""),
                    "object_name": str(row[3] or ""),
                    "cardinality": int(row[4]) if row[4] is not None else None,
                    "cost": float(row[5]) if row[5] is not None else None,
                }
                for row in rows[:200]
            ]
            root = next((item for item in operations if item["id"] == 0), operations[0])
            full_scan = any(
                item["operation"].upper() == "TABLE ACCESS" and item["options"].upper() == "FULL"
                for item in operations
            )
            index_used = any("INDEX" in item["operation"].upper() for item in operations)
            return GatewayCostEstimate(
                "oracle",
                True,
                estimated_rows=root["cardinality"],
                estimated_cost=root["cost"],
                full_scan=full_scan,
                index_used=index_used,
                reason="Oracle EXPLAIN PLAN using an isolated planner account",
                plan={"operations": operations},
            )
        except GatewayError:
            raise
        except QueryError as exc:
            raise GatewayError(exc.code, str(exc), operation="estimate_cost") from exc
        except Exception as exc:
            raise GatewayError("E_GATEWAY_COST", "Oracle cost estimation failed", operation="estimate_cost", retryable=True) from exc
