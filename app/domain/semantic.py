from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4
KINDS={'metric','alias','relationship','default_filter','sensitive_field','sql_example','value_dictionary'}
@dataclass
class SemanticEntry:
 tenant_id:str; datasource_id:str; kind:str; name:str; definition:dict; owner_id:str=''; description:str=''; version:int=1; logical_id:str=field(default_factory=lambda:str(uuid4())); previous_id:str=''; active:bool=True; id:str=field(default_factory=lambda:str(uuid4())); created_at:datetime=field(default_factory=lambda:datetime.now(timezone.utc))

@dataclass(frozen=True)
class SemanticMatch:
 entry:SemanticEntry; score:int; reasons:tuple[str,...]=()
