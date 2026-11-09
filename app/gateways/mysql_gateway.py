from app.mysql_db import query_connection, mysql_schema, mysql_schema_details, execute_mysql
from app.gateways.sqlite_gateway import SQLiteGateway
from app.config import settings
from app.guardrails import bound_query_rows, validate_sql
class MySQLGateway(SQLiteGateway):
    def __init__(self,*,gateway_id,name,schema_loader=mysql_schema,details_loader=mysql_schema_details,executor=execute_mysql,connect=query_connection,allowed_tables=None):
        self.descriptor=__import__('app.domain.data_gateway',fromlist=['GatewayDescriptor']).GatewayDescriptor(gateway_id,name,'mysql','mysql'); self.load=schema_loader; self.details=details_loader; self.exec=executor; self.conn=connect; self.allowed={x.lower() for x in (allowed_tables or {'sales','customers'})}
    def test_connection(self):
        try:
            with self.conn() as c:
                with c.cursor() as cur: cur.execute('SELECT 1')
            return __import__('app.domain.data_gateway',fromlist=['GatewayHealth']).GatewayHealth('healthy','连接成功')
        except Exception:return __import__('app.domain.data_gateway',fromlist=['GatewayHealth']).GatewayHealth('unavailable','MySQL 连接检查失败')
    health=test_connection
    def schema(self): return {k:sorted(v) for k,v in self.load().items() if k.lower() in self.allowed}
    def schema_details(self):
        raw = self.details()
        tables = {k:v for k,v in raw.get("tables", {}).items() if k.lower() in self.allowed}
        rel = [GatewayRelationship(**item) for item in raw.get("relationships", []) if item["from_table"].lower() in self.allowed and item["to_table"].lower() in self.allowed]
        return GatewaySchemaDetails(tables, rel)
    def preview(self,table,limit=20,offset=0):
        if table not in self.schema(): raise __import__('app.domain.data_gateway',fromlist=['GatewayError']).GatewayError('E_GATEWAY_TABLE','预览表不存在或不可访问',operation='preview')
        return self.query(__import__('app.domain.data_gateway',fromlist=['GatewayQuery']).GatewayQuery(f'SELECT * FROM `{table}`',max_rows=limit,offset=offset))
    def query(self,r):
        visible={table:set(columns) for table,columns in self.schema().items()}
        sql=validate_sql(r.sql,visible,settings.max_sql_length,dialect=self.descriptor.dialect,max_params=settings.max_params)
        sql=bound_query_rows(sql,dialect=self.descriptor.dialect,max_rows=r.max_rows,offset=r.offset)
        c,rows,t=self.exec(sql,r.params,r.max_rows,r.offset); return __import__('app.domain.data_gateway',fromlist=['GatewayResult']).GatewayResult(list(c),[list(x) for x in rows],len(rows),bool(t),r.operation_id)
    def estimate_cost(self, r):
        from app.domain.data_gateway import GatewayCostEstimate
        from app.mysql_db import _compile_mysql
        sql=bound_query_rows(validate_sql(r.sql,{table:set(columns) for table,columns in self.schema().items()},settings.max_sql_length,dialect="mysql",max_params=settings.max_params),dialect="mysql",max_rows=r.max_rows,offset=r.offset)
        compiled, params = _compile_mysql(sql, r.params or {})
        with self.conn() as conn:
            with conn.cursor() as cur:
                cur.execute("EXPLAIN FORMAT=JSON " + compiled, params or None)
                row = cur.fetchone()
        payload = row[0] if row else {}
        if isinstance(payload, str):
            import json
            try: payload=json.loads(payload)
            except json.JSONDecodeError: payload={"raw": payload[:500]}
        text = str(payload).upper()
        return GatewayCostEstimate("mysql", True, estimated_rows=None, estimated_cost=float(payload.get("query_block", {}).get("cost_info", {}).get("query_cost", 0) or 0) if isinstance(payload, dict) else None, full_scan="TABLE_SCAN" in text or "ACCESS_TYPE': 'ALL" in text, index_used="INDEX" in text, reason="MySQL EXPLAIN FORMAT=JSON", plan=payload if isinstance(payload, dict) else {})
