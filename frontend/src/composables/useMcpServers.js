import { ref } from 'vue'

const emptyServer = () => ({
  name: '',
  transport: 'streamable-http',
  endpoint: '',
  auth_env_ref: '',
  allowed_tools: '',
  enabled: true,
  read_only_only: true,
  allow_private_network: false,
  request_timeout_seconds: 10,
  max_concurrency: 4,
  daily_call_limit: 1000,
  circuit_failure_threshold: 3,
  circuit_reset_seconds: 30,
})

const emptyCall = () => ({ server_id: '', tool_name: '', arguments: '{}' })

function detailMessage(data, fallback) {
  if (typeof data?.detail === 'string') return data.detail
  return data?.detail?.message || fallback
}

export function useMcpServers(api, authFetch) {
  const mcpServers = ref([])
  const mcpDraft = ref(emptyServer())
  const mcpEditingId = ref('')
  const mcpError = ref('')
  const mcpLoading = ref(false)
  const mcpBusyId = ref('')
  const mcpCallDraft = ref(emptyCall())
  const mcpCallResult = ref(null)
  const mcpCallBusy = ref(false)

  async function request(path = '', options = {}) {
    const response = await authFetch(`${api}/api/v1/mcp-servers${path}`, options)
    const text = await response.text()
    const data = text ? JSON.parse(text) : null
    if (!response.ok) throw new Error(detailMessage(data, 'MCP 管理请求失败'))
    return data
  }

  function callableTools(server) {
    if (!server || !server.enabled || server.health_status !== 'healthy') return []
    const allowed = new Set(server.allowed_tools || [])
    return (server.discovered_tools || []).filter(tool => allowed.has(tool))
  }

  function syncCallSelection(preferredId = '') {
    const currentId = preferredId || mcpCallDraft.value.server_id
    const server = mcpServers.value.find(item => item.id === currentId) || mcpServers.value.find(item => item.enabled) || mcpServers.value[0]
    if (!server) {
      mcpCallDraft.value = emptyCall()
      return
    }
    const tools = callableTools(server)
    mcpCallDraft.value.server_id = server.id
    if (!tools.includes(mcpCallDraft.value.tool_name)) mcpCallDraft.value.tool_name = tools[0] || ''
  }

  async function loadMcpServers() {
    mcpLoading.value = true
    mcpError.value = ''
    try {
      const data = await request()
      mcpServers.value = Array.isArray(data) ? data : []
      syncCallSelection()
    } catch (error) {
      mcpError.value = error.message
    } finally {
      mcpLoading.value = false
    }
  }

  function editMcpServer(server) {
    mcpEditingId.value = server.id
    mcpDraft.value = {
      name: server.name,
      transport: server.transport,
      endpoint: server.endpoint,
      auth_env_ref: server.auth_env_ref,
      allowed_tools: (server.allowed_tools || []).join('\n'),
      enabled: server.enabled,
      read_only_only: true,
      allow_private_network: server.allow_private_network,
      request_timeout_seconds: server.request_timeout_seconds,
      max_concurrency: server.max_concurrency,
      daily_call_limit: server.daily_call_limit,
      circuit_failure_threshold: server.circuit_failure_threshold,
      circuit_reset_seconds: server.circuit_reset_seconds,
    }
  }

  function cancelMcpEdit() {
    mcpEditingId.value = ''
    mcpDraft.value = emptyServer()
  }

  function serverPayload() {
    const allowedTools = [...new Set(String(mcpDraft.value.allowed_tools || '')
      .split(/[\n,]+/)
      .map(item => item.trim())
      .filter(Boolean))]
    if (!allowedTools.length) throw new Error('请至少填写一个允许工具')
    return {
      name: String(mcpDraft.value.name || '').trim(),
      transport: 'streamable-http',
      endpoint: String(mcpDraft.value.endpoint || '').trim(),
      auth_env_ref: String(mcpDraft.value.auth_env_ref || '').trim(),
      allowed_tools: allowedTools,
      enabled: Boolean(mcpDraft.value.enabled),
      read_only_only: true,
      allow_private_network: Boolean(mcpDraft.value.allow_private_network),
      request_timeout_seconds: Number(mcpDraft.value.request_timeout_seconds),
      max_concurrency: Number(mcpDraft.value.max_concurrency),
      daily_call_limit: Number(mcpDraft.value.daily_call_limit),
      circuit_failure_threshold: Number(mcpDraft.value.circuit_failure_threshold),
      circuit_reset_seconds: Number(mcpDraft.value.circuit_reset_seconds),
    }
  }

  async function saveMcpServer() {
    mcpError.value = ''
    mcpBusyId.value = 'save'
    try {
      const payload = serverPayload()
      const id = mcpEditingId.value
      await request(id ? `/${encodeURIComponent(id)}` : '', {
        method: id ? 'PATCH' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      cancelMcpEdit()
      await loadMcpServers()
    } catch (error) {
      mcpError.value = error.message
    } finally {
      mcpBusyId.value = ''
    }
  }

  async function patchMcpServer(server, changes) {
    mcpError.value = ''
    mcpBusyId.value = server.id
    try {
      await request(`/${encodeURIComponent(server.id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(changes),
      })
      await loadMcpServers()
    } catch (error) {
      mcpError.value = error.message
    } finally {
      mcpBusyId.value = ''
    }
  }

  async function checkMcpHealth(server) {
    mcpError.value = ''
    mcpBusyId.value = server.id
    try {
      await request(`/${encodeURIComponent(server.id)}/health`, { method: 'POST' })
      await loadMcpServers()
      selectMcpCallServer(server.id)
    } catch (error) {
      mcpError.value = error.message
    } finally {
      mcpBusyId.value = ''
    }
  }

  async function removeMcpServer(server) {
    if (!window.confirm(`确定删除 MCP Server“${server.name}”吗？`)) return
    mcpError.value = ''
    mcpBusyId.value = server.id
    try {
      await request(`/${encodeURIComponent(server.id)}`, { method: 'DELETE' })
      if (mcpEditingId.value === server.id) cancelMcpEdit()
      await loadMcpServers()
    } catch (error) {
      mcpError.value = error.message
    } finally {
      mcpBusyId.value = ''
    }
  }

  function selectMcpCallServer(serverId) {
    mcpCallDraft.value.server_id = serverId
    mcpCallResult.value = null
    syncCallSelection(serverId)
  }

  async function invokeMcpTool() {
    mcpError.value = ''
    mcpCallResult.value = null
    let args
    try {
      args = JSON.parse(mcpCallDraft.value.arguments || '{}')
      if (!args || Array.isArray(args) || typeof args !== 'object') throw new Error()
    } catch {
      mcpError.value = '诊断参数必须是 JSON 对象'
      return
    }
    const server = mcpServers.value.find(item => item.id === mcpCallDraft.value.server_id)
    if (!server || !callableTools(server).includes(mcpCallDraft.value.tool_name)) {
      mcpError.value = '请先完成健康检查并选择已发现的白名单工具'
      return
    }
    mcpCallBusy.value = true
    try {
      mcpCallResult.value = await request(`/${encodeURIComponent(server.id)}/invoke`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tool_name: mcpCallDraft.value.tool_name, arguments: args }),
      })
      await loadMcpServers()
    } catch (error) {
      mcpError.value = error.message
    } finally {
      mcpCallBusy.value = false
    }
  }

  return {
    mcpServers, mcpDraft, mcpEditingId, mcpError, mcpLoading, mcpBusyId,
    mcpCallDraft, mcpCallResult, mcpCallBusy, callableTools,
    loadMcpServers, editMcpServer, cancelMcpEdit, saveMcpServer, patchMcpServer,
    checkMcpHealth, removeMcpServer, selectMcpCallServer, invokeMcpTool,
  }
}
