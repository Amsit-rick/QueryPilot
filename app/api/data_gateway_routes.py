from dataclasses import asdict
from fastapi import APIRouter,Depends,HTTPException,Query
from app.api.dependencies import require_query_access, require_manage_access
from app.datasources import list_datasources,get_datasource,role_can_use
from app.gateways.factory import build_gateway_registry
from app.services.data_gateway_service import DataGatewayService
from app.domain.data_gateway import GatewayQuery,GatewayError
from app.guardrails import validate_sql,QueryError
from app.gateways.remote_sql import normalize_remote_sql
from app.policy import filter_schema,apply_policy,redact_rows
from app.schemas.data_gateway import GatewayQueryRequest,GatewayCancelRequest
from app.config import settings
from app.retrieval import load_catalog
from app.services.relationship_service import resolve_relationships
router=APIRouter(prefix='/api/v1/data-gateways',tags=['data-gateways'])
def err(e):
    if e.code.endswith('NOT_FOUND') or e.code.endswith('TABLE'):
        status = 404
    elif 'FORBIDDEN' in e.code:
        status = 403
    elif 'UNSUPPORTED' in e.code:
        status = 409
    elif 'LIMIT' in e.code:
        status = 422
    elif e.code == 'E_GATEWAY_CONFIG':
        status = 500
    else:
        status = 502
    raise HTTPException(status_code=status, detail=e.as_dict())


def svc():
    try:
        return DataGatewayService(
            build_gateway_registry(list_datasources()),
            can_use=lambda i,d: bool((x:=get_datasource(d.id)) and role_can_use(i,x)),
        )
    except GatewayError as e:
        err(e)
@router.get('')
def listing(i=Depends(require_query_access)):
 service = svc()
 return [
  {**asdict(descriptor), "capabilities": asdict(service.registry.require(descriptor.id).capabilities)}
  for descriptor in service.list(i)
 ]
@router.get('/{id}/schema')
def schema(id:str,i=Depends(require_query_access)):
 try:return {'tables':filter_schema(svc().schema(i,id),{**i,'datasource':id})}
 except GatewayError as e:err(e)
@router.get('/{id}/schema-details')
def schema_details(id:str,i=Depends(require_query_access)):
 try:
  service=svc(); details=service.schema_details(i,id)
  allowed=filter_schema(service.schema(i,id),{**i,'datasource':id})
  visible=set(allowed)
  database=[item.__dict__ for item in details.relationships if item.from_table in visible and item.to_table in visible]
  relationships=resolve_relationships(allowed, database=database, manual=load_catalog(settings.catalog_path).get('relationships', []), datasource_id=id, tenant_id=i.get('tenant_id'))
  return {'tables': {name: value for name, value in details.tables.items() if name in visible}, 'relationships': relationships}
 except GatewayError as e:err(e)
@router.get('/{id}/health')
def health(id:str,i=Depends(require_query_access)):
 try:return asdict(svc().health(i,id))
 except GatewayError as e:err(e)
@router.post('/{id}/test')
def test(id:str,i=Depends(require_manage_access)):
 try:return asdict(svc().test_connection(i,id))
 except GatewayError as e:err(e)
@router.get('/{id}/preview')
def preview(id:str,table:str=Query(...),limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0),i=Depends(require_query_access)):
 try:
  service=svc(); allowed=filter_schema(service.schema(i,id),{**i,'datasource':id})
  if table not in allowed: raise GatewayError('E_GATEWAY_TABLE','预览表不存在或不可访问',operation='preview')
  quote='`' if service.registry.require(id).descriptor.dialect=='mysql' else '"'
  result=service.query(i,id,GatewayQuery(f'SELECT '+', '.join(f'{quote}{c}{quote}' for c in allowed[table])+f' FROM {quote}{table}{quote}',max_rows=limit,offset=offset))
  return asdict(result)
 except GatewayError as e:err(e)
@router.post('/{id}/query')
def query(id:str,r:GatewayQueryRequest,i=Depends(require_manage_access)):
 try:
  allowed=filter_schema(svc().schema(i,id),{**i,'datasource':id})
  backend=get_datasource(id).get('backend')
  sql=apply_policy(validate_sql(normalize_remote_sql(r.sql,backend),{k:set(v) for k,v in allowed.items()},settings.max_sql_length,dialect=backend,max_params=settings.max_params),{**i,'datasource':id})
  return asdict(svc().query(i,id,GatewayQuery(sql,r.params,r.max_rows,r.offset)))
 except QueryError as e: raise HTTPException(422,{'code':e.code,'message':str(e)}) from e
 except GatewayError as e:err(e)
@router.post('/{id}/cancel')
def cancel(id:str,r:GatewayCancelRequest,i=Depends(require_manage_access)):
 try:return {'cancelled':svc().cancel(i,id,r.operation_id)}
 except GatewayError as e:err(e)
