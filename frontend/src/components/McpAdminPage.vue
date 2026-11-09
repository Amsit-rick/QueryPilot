<script setup>
import { computed } from 'vue'

const props = defineProps({
  servers: { type: Array, default: () => [] },
  draft: { type: Object, required: true },
  editingId: { type: String, default: '' },
  error: { type: String, default: '' },
  loading: Boolean,
  busyId: { type: String, default: '' },
  callDraft: { type: Object, required: true },
  callResult: { type: Object, default: null },
  callBusy: Boolean,
})

const emit = defineEmits([
  'refresh', 'back', 'save', 'edit', 'cancel', 'patch', 'health', 'remove',
  'select-call-server', 'invoke',
])

const healthLabels = {
  healthy: '健康', unhealthy: '不可用', unconfigured: '缺少凭据', disabled: '已停用',
  policy_blocked: '策略阻断', unchecked: '未检查',
}
const healthyCount = computed(() => props.servers.filter(item => item.health_status === 'healthy').length)
const credentialCount = computed(() => props.servers.filter(item => item.credential_configured).length)
const selectedServer = computed(() => props.servers.find(item => item.id === props.callDraft.server_id) || null)
const callableTools = computed(() => {
  if (!selectedServer.value || !selectedServer.value.enabled || selectedServer.value.health_status !== 'healthy') return []
  const allowed = new Set(selectedServer.value.allowed_tools || [])
  return (selectedServer.value.discovered_tools || []).filter(tool => allowed.has(tool))
})

function fmtTime(value) {
  return value ? String(value).slice(0, 19).replace('T', ' ') : '—'
}

function resultPreview(value) {
  return value ? JSON.stringify(value, null, 2) : ''
}
</script>

<template>
  <main class="admin mcp-admin-page">
    <header>
      <div><span class="eyebrow">MCP GOVERNANCE</span><h1>MCP Server 管理</h1><p>登记外部只读 MCP、检查工具声明，并通过受控调用验证连接。</p></div>
      <div class="head-actions"><button class="ghost" :disabled="loading" @click="emit('refresh')">刷新</button><button class="run" @click="emit('back')">← 返回查询</button></div>
    </header>

    <div v-if="error" class="error" role="alert">{{error}}</div>

    <section class="mcp-summary-band" aria-label="MCP 运行摘要">
      <div><span>注册数量</span><b>{{servers.length}}</b></div>
      <div><span>健康连接</span><b :class="healthyCount?'state-ok':''">{{healthyCount}}</b></div>
      <div><span>凭据已注入</span><b>{{credentialCount}}</b></div>
      <div><span>执行边界</span><b>只读白名单</b></div>
    </section>

    <div class="mcp-admin-layout">
      <section class="card mcp-server-form-card">
        <div class="semantic-card-head"><h3>{{editingId?'编辑 Server':'注册 Server'}}</h3><button v-if="editingId" class="ghost" @click="emit('cancel')">取消编辑</button></div>
        <form class="mcp-server-form" @submit.prevent="emit('save')">
          <label>名称<input v-model="draft.name" maxlength="120" required placeholder="例如 Finance MCP"></label>
          <label>传输协议<input value="streamable-http" disabled></label>
          <label class="wide">Endpoint<input v-model="draft.endpoint" maxlength="500" type="url" required placeholder="https://mcp.example.com/mcp"></label>
          <label class="wide">凭据环境变量<input v-model="draft.auth_env_ref" maxlength="128" pattern="[A-Z][A-Z0-9_]+" required placeholder="FINANCE_MCP_TOKEN"><small>只保存变量名，不填写 Token 或 API Key。</small></label>
          <label class="wide">允许工具<textarea v-model="draft.allowed_tools" rows="4" required placeholder="schema.list&#10;report.read"></textarea><small>每行一个精确工具名，禁止通配符。</small></label>
          <label>请求超时（秒）<input v-model.number="draft.request_timeout_seconds" type="number" min="1" max="120" required></label>
          <label>最大并发<input v-model.number="draft.max_concurrency" type="number" min="1" max="32" required></label>
          <label>每日调用上限<input v-model.number="draft.daily_call_limit" type="number" min="1" max="1000000" required></label>
          <label>熔断失败阈值<input v-model.number="draft.circuit_failure_threshold" type="number" min="1" max="20" required></label>
          <label>熔断恢复（秒）<input v-model.number="draft.circuit_reset_seconds" type="number" min="1" max="3600" required></label>
          <label class="check"><input v-model="draft.enabled" type="checkbox">启用 Server</label>
          <label class="check"><input v-model="draft.allow_private_network" type="checkbox">允许访问私网目标</label>
          <p v-if="draft.allow_private_network" class="mcp-policy-warning">允许私网会扩大 SSRF 风险；云平台 Metadata 和基础设施地址仍会被拒绝。</p>
          <button class="run" type="submit" :disabled="Boolean(busyId)">{{busyId==='save'?'保存中…':editingId?'保存修改':'注册 Server'}}</button>
        </form>
      </section>

      <section class="card mcp-server-list-card">
        <div class="model-section-head"><h2>外部 Server</h2><span>{{servers.length}} 个</span></div>
        <div v-if="loading" class="model-empty">正在加载 MCP 配置…</div>
        <div v-else-if="!servers.length" class="model-empty">暂无外部 MCP Server。</div>
        <div v-else class="mcp-server-list">
          <article v-for="server in servers" :key="server.id" class="mcp-server-row">
            <div class="mcp-server-main">
              <div class="mcp-server-title"><strong>{{server.name}}</strong><span class="health-state" :class="server.enabled?server.health_status:'disabled'">{{healthLabels[server.enabled?server.health_status:'disabled']||server.health_status}}</span><span v-if="server.credential_configured" class="model-badge">凭据已注入</span></div>
              <code>{{server.endpoint}}</code>
              <small>{{server.auth_env_ref}} · {{server.transport}} · 并发 {{server.max_concurrency}} · 每日 {{server.daily_call_limit}}</small>
              <small v-if="server.last_checked_at">最近检查 {{fmtTime(server.last_checked_at)}} · {{server.last_latency_ms}} ms<template v-if="server.last_error_code"> · {{server.last_error_code}}</template></small>
              <div class="mcp-tool-groups">
                <div><span>允许</span><i v-for="tool in server.allowed_tools" :key="`a-${tool}`">{{tool}}</i></div>
                <div v-if="server.discovered_tools?.length"><span>发现</span><i v-for="tool in server.discovered_tools" :key="`d-${tool}`">{{tool}}</i></div>
              </div>
            </div>
            <div class="provider-actions">
              <button class="ghost" :disabled="Boolean(busyId)||!server.enabled" @click="emit('health',server)">健康检查</button>
              <button class="ghost" :disabled="Boolean(busyId)" @click="emit('edit',server)">编辑</button>
              <button class="ghost" :disabled="Boolean(busyId)" @click="emit('patch',server,{enabled:!server.enabled})">{{server.enabled?'停用':'启用'}}</button>
              <button class="ghost danger" :disabled="Boolean(busyId)" @click="emit('remove',server)">删除</button>
            </div>
          </article>
        </div>
      </section>
    </div>

    <section class="card mcp-diagnostic-card">
      <div class="model-section-head"><div><h2>只读工具诊断</h2><p>只显示健康检查发现且位于白名单内的工具；调用不会自动重试。</p></div><span>管理员操作</span></div>
      <form class="mcp-diagnostic-form" @submit.prevent="emit('invoke')">
        <label>诊断 Server<select :value="callDraft.server_id" @change="emit('select-call-server',$event.target.value)"><option value="">请选择</option><option v-for="server in servers" :key="server.id" :value="server.id" :disabled="!server.enabled">{{server.name}}</option></select></label>
        <label>诊断工具<select v-model="callDraft.tool_name" :disabled="!callableTools.length"><option value="">{{callableTools.length?'请选择工具':'请先完成健康检查'}}</option><option v-for="tool in callableTools" :key="tool" :value="tool">{{tool}}</option></select></label>
        <label class="wide">诊断参数 JSON<textarea v-model="callDraft.arguments" rows="6" spellcheck="false"></textarea></label>
        <button class="run" type="submit" :disabled="callBusy||!callDraft.tool_name">{{callBusy?'调用中…':'调用只读工具'}}</button>
      </form>
      <div v-if="callResult" class="mcp-call-result"><div><strong>调用成功</strong><span>{{callResult.tool_name}} · {{callResult.duration_ms}} ms</span></div><pre>{{resultPreview(callResult.result)}}</pre></div>
    </section>
  </main>
</template>
