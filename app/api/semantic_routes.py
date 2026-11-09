from dataclasses import asdict
from fastapi import APIRouter,Depends,HTTPException
from app.auth import require_identity
from app.semantic_runtime import get_semantic_service
from app.schemas.semantics import SemanticCreateRequest,SemanticUpdateRequest
from app.datasources import get_datasource,role_can_use,datasource_schema
from app.policy import filter_schema
router=APIRouter(prefix='/api/v1/semantics',tags=['semantics'])
def ser(e,match=None):
 d=asdict(e); d['created_at']=e.created_at.isoformat()
 if match:
  d['match_score']=match.score; d['match_reasons']=list(match.reasons)
 return d
def admin(i):
 if 'admin' not in i.get('roles',[]): raise HTTPException(403,'需要管理员权限')
 return i
@router.get('')
def listing(datasource_id:str='',i=Depends(require_identity)):
 admin(i); return [ser(e) for e in get_semantic_service().list(i.get('tenant_id','default'),datasource_id)]
@router.post('')
def create(r:SemanticCreateRequest,i=Depends(require_identity)):
 admin(i); ds=get_datasource(r.datasource_id)
 if not ds or not role_can_use(i,ds): raise HTTPException(403,'无权访问数据源')
 try:return ser(get_semantic_service().create(tenant_id=i.get('tenant_id','default'),owner_id=str(i.get('subject','')),**r.model_dump()))
 except ValueError as e: raise HTTPException(422,str(e))
@router.patch('/{id}')
def update(id:str,r:SemanticUpdateRequest,i=Depends(require_identity)):
 admin(i)
 try:return ser(get_semantic_service().update(id,i.get('tenant_id','default'),r.model_dump(exclude_none=True),str(i.get('subject',''))))
 except LookupError as e: raise HTTPException(404,str(e))
 except ValueError as e: raise HTTPException(409 if 'CONFLICT' in str(e) else 422,str(e))
@router.get('/search')
def search(datasource_id:str,q:str='',i=Depends(require_identity)):
 ds=get_datasource(datasource_id)
 if not ds or not role_can_use(i,ds): return {'entries':[]}
 schema=filter_schema(datasource_schema(ds),{**i,'datasource':datasource_id})
 return {'entries':[ser(match.entry,match) for match in get_semantic_service().search_matches(q,i.get('tenant_id','default'),datasource_id,schema)]}
@router.get('/{id}/history')
def history(id:str,i=Depends(require_identity)):
 admin(i); return [ser(e) for e in get_semantic_service().history(id,i.get('tenant_id','default'))]
