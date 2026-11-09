from pathlib import Path
from app.gateways.sqlite_gateway import SQLiteGateway
class DuckDBGateway(SQLiteGateway):
 def __init__(self,*,gateway_id,name,root_dir,datasets):
  self.root=Path(root_dir).resolve(); self.datasets=datasets; self.descriptor=__import__('app.domain.data_gateway',fromlist=['GatewayDescriptor']).GatewayDescriptor(gateway_id,name,'duckdb','duckdb')
  for f in datasets.values():
   p=(self.root/f).resolve()
   if p.parent!=self.root or p.suffix.lower()!='.csv': raise __import__('app.domain.data_gateway',fromlist=['GatewayError']).GatewayError('E_GATEWAY_PATH','数据文件路径不在允许目录内',operation='configure')
 def _db(self):
  import duckdb
  c=duckdb.connect(':memory:')
  for n,f in self.datasets.items(): c.execute(f'CREATE TABLE "{n}" AS SELECT * FROM read_csv_auto(?)',[str((self.root/f).resolve())])
  c.execute('SET enable_external_access=false'); return c
 def test_connection(self):
  from app.domain.data_gateway import GatewayHealth
  try:
   if any(not (self.root/f).is_file() for f in self.datasets.values()): raise FileNotFoundError
   c=self._db(); c.close(); return GatewayHealth('healthy','连接成功')
  except Exception:return GatewayHealth('unavailable','DuckDB 数据文件检查失败')
 health=test_connection
 def schema(self):
  c=self._db(); out={n:[x[0] for x in c.execute(f'DESCRIBE "{n}"').fetchall()] for n in self.datasets}; c.close(); return out
 def query(self,r):
  from app.domain.data_gateway import GatewayResult,GatewayError
  try:
   c=self._db(); cur=c.execute(r.sql)
   if r.offset: cur.fetchmany(r.offset)
   rows=cur.fetchmany(r.max_rows+1); cols=[x[0] for x in cur.description]; c.close(); return GatewayResult(cols,[list(x) for x in rows[:r.max_rows]],min(len(rows),r.max_rows),len(rows)>r.max_rows,r.operation_id)
  except Exception as e: raise GatewayError('E_GATEWAY_QUERY','DuckDB 查询被拒绝或执行失败',operation='query') from e
