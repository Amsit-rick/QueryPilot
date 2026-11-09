from app.postgres_db import _connect, execute_postgres, postgres_schema, postgres_schema_details
from app.gateways.mysql_gateway import MySQLGateway
from app.domain.data_gateway import GatewayError, GatewayDescriptor, GatewayQuery
from app.config import settings
from app.guardrails import bound_query_rows, validate_sql

class PostgreSQLGateway(MySQLGateway):
    def __init__(self, *, gateway_id, name, schema_loader=postgres_schema, details_loader=postgres_schema_details, executor=execute_postgres, connect=_connect, allowed_tables=None):
        super().__init__(gateway_id=gateway_id, name=name, schema_loader=schema_loader, details_loader=details_loader, executor=executor, connect=connect, allowed_tables=allowed_tables)
        self.descriptor = GatewayDescriptor(gateway_id, name, 'postgres', 'postgres')
    def preview(self, table, limit=20, offset=0):
        if table not in self.schema(): raise GatewayError('E_GATEWAY_TABLE', '预览表不存在或不可访问', operation='preview')
        return self.query(GatewayQuery(f'SELECT * FROM "{table}"', max_rows=limit, offset=offset))
    def estimate_cost(self, r):
        from app.domain.data_gateway import GatewayCostEstimate
        from app.postgres_db import _compile_postgres
        sql=bound_query_rows(validate_sql(r.sql,{table:set(columns) for table,columns in self.schema().items()},settings.max_sql_length,dialect="postgres",max_params=settings.max_params),dialect="postgres",max_rows=r.max_rows,offset=r.offset)
        compiled, params = _compile_postgres(sql, r.params or {})
        with self.conn() as conn, conn.cursor() as cur:
            cur.execute("EXPLAIN (FORMAT JSON) " + compiled, params or None)
            row=cur.fetchone()
        plan = row[0] if row else {}
        if isinstance(plan, list) and plan: plan=plan[0]
        root = plan.get("Plan", {}) if isinstance(plan, dict) else {}
        return GatewayCostEstimate("postgres", True, estimated_rows=int(root.get("Plan Rows", 0) or 0), estimated_cost=float(root.get("Total Cost", 0) or 0), full_scan=str(root.get("Node Type", "")).lower().endswith("seq scan"), index_used="index" in str(root.get("Node Type", "")).lower(), reason="PostgreSQL EXPLAIN FORMAT JSON", plan=plan if isinstance(plan, dict) else {})
