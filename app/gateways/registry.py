from app.domain.data_gateway import GatewayError
class GatewayRegistry:
    def __init__(self, gateways=()):
        self._items={}
        for gateway in gateways: self.register(gateway)
    def register(self,gateway):
        if gateway.descriptor.id in self._items: raise ValueError('E_GATEWAY_DUPLICATE')
        self._items[gateway.descriptor.id]=gateway; return gateway
    def get(self,id): return self._items.get(id)
    def require(self,id):
        item=self.get(id)
        if not item: raise GatewayError('E_GATEWAY_NOT_FOUND','数据网关不存在',operation='resolve')
        return item
    def list_descriptors(self): return [g.descriptor for g in self._items.values()]
