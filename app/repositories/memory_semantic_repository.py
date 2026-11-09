class InMemorySemanticRepository:
 def __init__(self): self.entries={}
 def clear(self): self.entries.clear()
 def create(self,e): self.entries[e.id]=e; return e
 def get(self,id,tenant_id):
  e=self.entries.get(id); return e if e and e.tenant_id==tenant_id else None
 def list_current(self,tenant_id,datasource_id=''):
  vals=[e for e in self.entries.values() if e.tenant_id==tenant_id and (not datasource_id or e.datasource_id==datasource_id)]
  latest={}
  for e in vals:
   if e.active and e.version>=latest.get(e.logical_id,e).version: latest[e.logical_id]=e
  return sorted(latest.values(),key=lambda e:(e.kind,e.name))
 def history(self,logical_id,tenant_id):
  current=self.entries.get(logical_id)
  key=current.logical_id if current and current.tenant_id==tenant_id else logical_id
  return sorted([e for e in self.entries.values() if e.tenant_id==tenant_id and e.logical_id==key],key=lambda e:e.version,reverse=True)
