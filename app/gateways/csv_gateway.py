from .duckdb_gateway import DuckDBGateway
class CSVGateway(DuckDBGateway):
 def __init__(self,*,gateway_id,name,root_dir,file_name,dataset_name): super().__init__(gateway_id=gateway_id,name=name,root_dir=root_dir,datasets={dataset_name:file_name}); self.descriptor=__import__('app.domain.data_gateway',fromlist=['GatewayDescriptor']).GatewayDescriptor(gateway_id,name,'csv','duckdb')
