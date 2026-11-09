from pydantic import BaseModel, Field
class SemanticCreateRequest(BaseModel):
 datasource_id:str; kind:str; name:str=Field(min_length=1,max_length=120); description:str=''; definition:dict
class SemanticUpdateRequest(BaseModel):
 datasource_id:str|None=None; kind:str|None=None; name:str|None=None; description:str|None=None; definition:dict|None=None; active:bool|None=None
