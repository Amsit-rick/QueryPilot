from app.domain.semantic import SemanticEntry,SemanticMatch,KINDS
from app.guardrails import QueryError, validate_sql
class SemanticService:
 def __init__(self,repository): self.repository=repository
 def _validate(self,kind,d):
  if not isinstance(d,dict): raise ValueError('E_SEMANTIC_DEFINITION:定义必须是 JSON 对象')
  required={'metric':{'formula','tables','columns'},'alias':{'target_table','target_column','tables','columns'},'sql_example':{'question','sql','tables','columns'},'value_dictionary':{'target_table','target_column','tables','columns','values'}}.get(kind,set())
  if not required<=set(d): raise ValueError('E_SEMANTIC_DEFINITION:定义缺少必填字段')
  allowed={'metric':required|{'keywords','aliases'},'alias':required|{'keywords','aliases'},'sql_example':required|{'params','keywords','aliases'},'value_dictionary':required|{'keywords'}}.get(kind,set(d))
  if set(d)-allowed: raise ValueError('未知字段')
  for field in ('keywords','aliases'):
   if field in d and (not isinstance(d[field],list) or not all(isinstance(value,str) and value.strip() for value in d[field])):
    raise ValueError(f'E_SEMANTIC_TERMS:{field} 必须是字符串数组')
  if kind == 'sql_example':
   if not all(isinstance(d[key],str) and d[key].strip() for key in ('question','sql')):
    raise ValueError('E_SEMANTIC_EXAMPLE:问题和 SQL 必须是非空文本')
   if not isinstance(d['tables'],list) or not d['tables'] or not all(isinstance(value,str) and value.strip() for value in d['tables']):
    raise ValueError('E_SEMANTIC_EXAMPLE:tables 必须是非空字符串数组')
   if not isinstance(d['columns'],list) or not d['columns'] or not all(isinstance(value,str) and value.strip() for value in d['columns']):
    raise ValueError('E_SEMANTIC_EXAMPLE:columns 必须是非空字符串数组')
   if 'params' in d and not isinstance(d['params'],dict):
    raise ValueError('E_SEMANTIC_EXAMPLE:params 必须是 JSON 对象')
   try:
    validate_sql(d['sql'], {table:set(d['columns']) for table in d['tables']})
   except QueryError as exc:
    raise ValueError(f'E_SEMANTIC_EXAMPLE_SQL:{exc}') from exc
  if kind == 'value_dictionary':
   table=d.get('target_table'); column=d.get('target_column'); tables=d.get('tables'); columns=d.get('columns'); values=d.get('values')
   if not isinstance(table,str) or not table.strip() or not isinstance(column,str) or not column.strip():
    raise ValueError('E_SEMANTIC_VALUES:目标表和字段必须是非空文本')
   if not isinstance(tables,list) or table not in tables or not isinstance(columns,list) or column not in columns:
    raise ValueError('E_SEMANTIC_VALUES:目标表和字段必须包含在授权引用中')
   if not isinstance(values,list) or not 1 <= len(values) <= 200:
    raise ValueError('E_SEMANTIC_VALUES:值词典必须包含 1 到 200 个值')
   canonical=[]
   for item in values:
    if not isinstance(item,dict) or set(item)-{'value','aliases'} or 'value' not in item:
     raise ValueError('E_SEMANTIC_VALUES:值项仅允许 value 和 aliases')
    value=item.get('value'); aliases=item.get('aliases',[])
    if not isinstance(value,(str,int,float,bool)) or isinstance(value,str) and not value.strip():
     raise ValueError('E_SEMANTIC_VALUES:value 必须是非空标量')
    if not isinstance(aliases,list) or not all(isinstance(alias,str) and alias.strip() for alias in aliases) or len(aliases)>20:
     raise ValueError('E_SEMANTIC_VALUES:aliases 必须是不超过 20 项的字符串数组')
    canonical.append((type(value).__name__,str(value)))
   if len(canonical)!=len(set(canonical)):
    raise ValueError('E_SEMANTIC_VALUES:标准值不能重复')
 def create(self,**kw):
  kind=kw['kind'];
  if kind not in KINDS: raise ValueError('E_SEMANTIC_KIND:不支持的类型')
  self._validate(kind,kw['definition']); return self.repository.create(SemanticEntry(**kw))
 def update(self,id,tenant_id,changes,owner_id):
  cur=self.repository.get(id,tenant_id)
  if not cur: raise LookupError('语义条目不存在')
  if cur.version!=max(e.version for e in self.repository.history(cur.logical_id,tenant_id)): raise ValueError('E_SEMANTIC_VERSION_CONFLICT')
  data={'tenant_id':cur.tenant_id,'datasource_id':changes.get('datasource_id',cur.datasource_id),'kind':changes.get('kind',cur.kind),'name':changes.get('name',cur.name),'definition':changes.get('definition',cur.definition),'description':changes.get('description',cur.description),'owner_id':owner_id,'version':cur.version+1,'logical_id':cur.logical_id,'previous_id':cur.id,'active':changes.get('active',cur.active)}
  self._validate(data['kind'],data['definition']); return self.repository.create(SemanticEntry(**data))
 def history(self,id,tenant_id): return self.repository.history(id,tenant_id)
 def list(self,tenant_id,datasource_id=''): return self.repository.list_current(tenant_id,datasource_id)
 def search_matches(self,question,tenant_id,datasource_id,allowed_schema,limit=20,kinds=None):
  q=question.lower().strip(); matches=[]
  for e in self.list(tenant_id,datasource_id):
   if e.kind == 'value_dictionary' and kinds is None: continue
   if kinds is not None and e.kind not in set(kinds): continue
   refs=set(e.definition.get('tables',[])); cols=set(e.definition.get('columns',[])); visible=all(t in allowed_schema and cols<=set(allowed_schema[t]) for t in refs)
   if not visible: continue
   if not q:
    matches.append(SemanticMatch(e,0,()))
    continue
   candidates=[('name',e.name,100),('description',e.description,40)]
   for field,weight in (('aliases',90),('keywords',85),('question',70),('sql',15)):
    values=e.definition.get(field,[] if field in {'aliases','keywords'} else '')
    if isinstance(values,str): values=[values] if values else []
    if isinstance(values,list): candidates.extend((field,value,weight) for value in values if isinstance(value,str) and value.strip())
   best=0; reasons=[]
   for field,text,weight in candidates:
    normalized=text.lower().strip()
    if not normalized: continue
    if normalized == q: score=weight + 20
    elif q in normalized: score=weight
    elif normalized in q: score=weight
    else: continue
    if score > best: best=score; reasons=[field]
    elif score == best and field not in reasons: reasons.append(field)
   if best: matches.append(SemanticMatch(e,best,tuple(reasons)))
  matches.sort(key=lambda item:(-item.score,item.entry.kind,item.entry.name))
  return matches[:limit]
 def search_value_matches(self,question,tenant_id,datasource_id,allowed_schema,limit=20):
  q=question.lower().strip(); matches=[]
  for e in self.list(tenant_id,datasource_id):
   if e.kind!='value_dictionary': continue
   definition=e.definition; table=str(definition.get('target_table','')); column=str(definition.get('target_column',''))
   if table not in allowed_schema or column not in set(allowed_schema[table]): continue
   for item in definition.get('values',[]):
    value=item.get('value'); candidates=[(str(value),90),*((alias,100) for alias in item.get('aliases',[]))]
    found=[(score,text) for text,score in candidates if str(text).strip() and str(text).lower() in q]
    if not found: continue
    score,matched_text=max(found,key=lambda pair:(pair[0],len(pair[1])))
    matches.append({'id':e.id,'logical_id':e.logical_id,'version':e.version,'name':e.name,'table':table,'column':column,'value':value,'matched_text':matched_text,'score':score})
  matches.sort(key=lambda item:(-item['score'],item['name'],str(item['value'])))
  return matches[:limit]
 def search(self,question,tenant_id,datasource_id,allowed_schema,limit=20,kinds=None):
  return [match.entry for match in self.search_matches(question,tenant_id,datasource_id,allowed_schema,limit,kinds)]
