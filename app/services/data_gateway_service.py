from app.domain.data_gateway import *
class DataGatewayService:
 def __init__(self,registry,*,can_use,max_rows=1000,max_preview_rows=100): self.registry=registry; self.can_use=can_use; self.max_rows=max_rows; self.max_preview_rows=max_preview_rows
 def _r(self,i,id):
  g=self.registry.require(id)
  if not self.can_use(i,g.descriptor): raise GatewayError('E_GATEWAY_FORBIDDEN','无权访问该数据网关',operation='authorize')
  return g
 def _health(self, i, id, operation, callback):
  try:
   value=callback(self._r(i,id))
   if not isinstance(value, GatewayHealth): raise TypeError
   return value
  except GatewayError:raise
  except TypeError as e:raise GatewayError('E_GATEWAY_CONTRACT','health 返回格式无效',operation=operation) from e
  except Exception as e:raise GatewayError('E_GATEWAY_HEALTH','数据网关健康检查失败',operation=operation,retryable=True) from e
 def list(self,i): return [d for d in self.registry.list_descriptors() if self.can_use(i,d)]
 def schema(self,i,id):
  try:
   value=self._r(i,id).schema()
   if not isinstance(value,dict) or any(not isinstance(k,str) or not isinstance(v,list) or any(not isinstance(c,str) for c in v) for k,v in value.items()): raise TypeError
   return value
  except GatewayError:raise
  except TypeError as e:raise GatewayError('E_GATEWAY_CONTRACT','schema 返回格式无效',operation='schema') from e
  except Exception as e:raise GatewayError('E_GATEWAY_SCHEMA','数据网关 schema 操作失败',operation='schema',retryable=True) from e
 def schema_details(self,i,id):
  try:
   g=self._r(i,id)
   if not g.capabilities.relationships or not hasattr(g, 'schema_details'): raise GatewayError('E_GATEWAY_UNSUPPORTED','数据网关不支持关系元数据',operation='schema_details')
   value=g.schema_details()
   if not isinstance(value, GatewaySchemaDetails): raise TypeError
   visible=set(self.schema(i,id))
   return GatewaySchemaDetails({name: detail for name, detail in value.tables.items() if name in visible}, [item for item in value.relationships if item.from_table in visible and item.to_table in visible])
  except GatewayError: raise
  except TypeError as e: raise GatewayError('E_GATEWAY_CONTRACT','schema_details 返回格式无效',operation='schema_details') from e
  except Exception as e: raise GatewayError('E_GATEWAY_SCHEMA','数据网关关系元数据读取失败',operation='schema_details',retryable=True) from e
 def estimate_cost(self,i,id,r):
  try:
   g=self._r(i,id)
   if not g.capabilities.cost_estimate or not hasattr(g, 'estimate_cost'):
    return GatewayCostEstimate(g.descriptor.dialect, False, reason='adapter does not support EXPLAIN')
   value=g.estimate_cost(r)
   if not isinstance(value, GatewayCostEstimate): raise TypeError
   return value
  except GatewayError: raise
  except TypeError as e: raise GatewayError('E_GATEWAY_CONTRACT','estimate_cost 返回格式无效',operation='estimate_cost') from e
  except Exception as e: raise GatewayError('E_GATEWAY_COST','查询成本评估失败',operation='estimate_cost',retryable=True) from e
 def query(self,i,id,r):
  if r.max_rows>self.max_rows:raise GatewayError('E_GATEWAY_LIMIT','查询行数超出数据网关上限',operation='query')
  try:
   value=self._r(i,id).query(r)
   if not isinstance(value,GatewayResult): raise TypeError
   return value
  except GatewayError:raise
  except TypeError as e:raise GatewayError('E_GATEWAY_CONTRACT','query 返回格式无效',operation='query') from e
  except Exception as e:raise GatewayError('E_GATEWAY_QUERY','数据网关 query 操作失败',operation='query') from e
 def preview(self,i,id,t,limit=20,offset=0):
  if limit < 1 or limit>self.max_preview_rows or offset < 0:raise GatewayError('E_GATEWAY_LIMIT','预览参数超出允许范围',operation='preview')
  try:
   g=self._r(i,id)
   if not g.capabilities.preview: raise GatewayError('E_GATEWAY_UNSUPPORTED','数据网关不支持 preview',operation='preview')
   value=g.preview(t,limit,offset)
   if not isinstance(value,GatewayResult): raise TypeError
   return value
  except GatewayError:raise
  except TypeError as e:raise GatewayError('E_GATEWAY_CONTRACT','preview 返回格式无效',operation='preview') from e
  except Exception as e:raise GatewayError('E_GATEWAY_PREVIEW','数据网关 preview 操作失败',operation='preview',retryable=True) from e
 def test_connection(self,i,id): return self._health(i,id,'connection_test',lambda gateway: gateway.test_connection())
 def health(self,i,id): return self._health(i,id,'health',lambda gateway: gateway.health())
 def cancel(self,i,id,op):
  if not str(op).strip(): raise GatewayError('E_GATEWAY_LIMIT','取消操作缺少 operation_id',operation='cancel')
  try:
   g=self._r(i,id)
   if not g.capabilities.cancel: raise GatewayError('E_GATEWAY_UNSUPPORTED','数据网关不支持 cancel',operation='cancel')
   value=g.cancel(op)
   if not isinstance(value,bool): raise TypeError
   return value
  except GatewayError:raise
  except TypeError as e:raise GatewayError('E_GATEWAY_CONTRACT','cancel 返回格式无效',operation='cancel') from e
  except Exception as e:raise GatewayError('E_GATEWAY_CANCEL','数据网关 cancel 操作失败',operation='cancel',retryable=True) from e
