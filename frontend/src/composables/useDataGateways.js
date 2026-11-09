import { ref } from 'vue'

function messageOf(data, fallback) {
  return data?.detail?.message || data?.detail?.detail?.message || (typeof data?.detail === 'string' ? data.detail : '') || fallback
}

export function useDataGateways(api, authFetch) {
  const gateways = ref([]); const gatewayState = ref({}); const gatewayError = ref(''); const gatewayLoading = ref(false)

  function patch(id, values) { gatewayState.value = { ...gatewayState.value, [id]: { ...(gatewayState.value[id] || {}), ...values } } }

  async function request(id, path, options, fallback) {
    patch(id, { busy: true, error: '' })
    try {
      const response = await authFetch(`${api}/api/v1/data-gateways/${encodeURIComponent(id)}${path}`, options)
      const data = await response.json().catch(() => ({}))
      if (!response.ok) { patch(id, { error: messageOf(data, fallback) }); return null }
      return data
    } catch { patch(id, { error: '无法连接数据网关服务' }); return null }
    finally { patch(id, { busy: false }) }
  }

  async function loadGateways() {
    gatewayLoading.value = true; gatewayError.value = ''
    try {
      const response = await authFetch(`${api}/api/v1/data-gateways`)
      const data = await response.json().catch(() => ([]))
      if (!response.ok) { gatewayError.value = messageOf(data, '数据网关加载失败'); return }
      gateways.value = Array.isArray(data) ? data : []
      await Promise.all(gateways.value.map(item => refreshHealth(item.id)))
    } catch { gatewayError.value = '无法连接数据网关服务' }
    finally { gatewayLoading.value = false }
  }

  async function refreshHealth(id) { const data = await request(id, '/health', {}, '健康状态读取失败'); if (data) patch(id, { health: data }) }
  async function testGateway(id) { const data = await request(id, '/test', { method: 'POST' }, '连接测试失败'); if (data) patch(id, { health: data, tested: true }) }
  async function loadGatewaySchema(id) {
    const data = await request(id, '/schema', {}, 'Schema 读取失败')
    if (data) { const tables = data.tables || {}; patch(id, { schema: tables, selectedTable: gatewayState.value[id]?.selectedTable || Object.keys(tables)[0] || '', preview: null }) }
  }
  function selectGatewayTable(id, table) { patch(id, { selectedTable: table, preview: null }) }
  async function previewGateway(id, offset = 0) {
    const table = gatewayState.value[id]?.selectedTable
    if (!table) { patch(id, { error: '请先选择数据表' }); return }
    const data = await request(id, `/preview?table=${encodeURIComponent(table)}&limit=20&offset=${offset}`, {}, '数据预览失败')
    if (data) patch(id, { preview: data, previewOffset: offset })
  }

  return { gateways, gatewayState, gatewayError, gatewayLoading, loadGateways, refreshHealth, testGateway, loadGatewaySchema, selectGatewayTable, previewGateway }
}
