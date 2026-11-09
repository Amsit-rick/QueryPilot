from pydantic import BaseModel,Field
class GatewayQueryRequest(BaseModel): sql:str=Field(min_length=1,max_length=5000); params:dict={}; max_rows:int=Field(1000,ge=1); offset:int=Field(0,ge=0)
class GatewayCancelRequest(BaseModel): operation_id:str=Field(min_length=1)
