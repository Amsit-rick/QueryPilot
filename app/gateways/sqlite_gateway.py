from pathlib import Path
from uuid import uuid4
from app.db import schema, schema_details, execute_readonly
from app.domain.data_gateway import *
from app.config import settings
from app.guardrails import bound_query_rows, validate_sql
class SQLiteGateway:
    capabilities=GatewayCapabilities(cancel=False, cost_estimate=True, relationships=True)
    def __init__(self,*,gateway_id,name,database_path,schema_loader=schema,details_loader=schema_details,executor=execute_readonly,timeout_seconds=30): self.descriptor=GatewayDescriptor(gateway_id,name,'sqlite','sqlite'); self.path=database_path; self.load=schema_loader; self.details=details_loader; self.exec=executor; self.timeout=timeout_seconds
    def test_connection(self):
        try:
            if not Path(self.path).is_file(): raise FileNotFoundError
            self.load(self.path); return GatewayHealth('healthy','连接成功')
        except Exception: return GatewayHealth('unavailable','SQLite 连接检查失败')
    health=test_connection
    def schema(self): return {k:sorted(v) for k,v in self.load(self.path).items()}
    def schema_details(self):
        raw = self.details(self.path)
        allowed = set(self.schema())
        return GatewaySchemaDetails({k:v for k,v in raw.get("tables", {}).items() if k in allowed}, [GatewayRelationship(**item) for item in raw.get("relationships", []) if item["from_table"] in allowed and item["to_table"] in allowed])
    def preview(self,table,limit=20,offset=0):
        if table not in self.schema(): raise GatewayError('E_GATEWAY_TABLE','预览表不存在或不可访问',operation='preview')
        return self.query(GatewayQuery(f'SELECT * FROM "{table}"',max_rows=limit,offset=offset))
    def query(self,r):
        visible={table:set(columns) for table,columns in self.schema().items()}
        sql=validate_sql(r.sql,visible,settings.max_sql_length,dialect='sqlite',max_params=settings.max_params)
        sql=bound_query_rows(sql,dialect='sqlite',max_rows=r.max_rows,offset=r.offset)
        c,rows,t=self.exec(self.path,sql,r.max_rows,r.params,self.timeout,r.offset); rows=[list(x) for x in rows]; return GatewayResult(list(c),rows,len(rows),bool(t),r.operation_id or str(uuid4()))
    def estimate_cost(self, r):
        import sqlite3
        from sqlglot import parse_one
        sql = bound_query_rows(validate_sql(r.sql, {table:set(columns) for table,columns in self.schema().items()}, settings.max_sql_length, dialect="sqlite", max_params=settings.max_params), dialect="sqlite", max_rows=r.max_rows, offset=r.offset)
        with sqlite3.connect(f"file:{Path(self.path).resolve()}?mode=ro", uri=True) as conn:
            rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}", r.params or {}).fetchall()
        details = " ".join(str(row[3]).upper() for row in rows)
        full_scan = "SCAN " in details and "USING INDEX" not in details
        return GatewayCostEstimate("sqlite", True, estimated_rows=None, estimated_cost=float(len(rows)), full_scan=full_scan, index_used="USING INDEX" in details or "USING INTEGER PRIMARY KEY" in details, reason="SQLite query plan", plan={"rows": [list(row) for row in rows]})
    def cancel(self,operation_id): return False
