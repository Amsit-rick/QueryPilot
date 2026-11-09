<script setup>
defineProps({
  approvals: { type: Array, default: () => [] },
  error: { type: String, default: '' },
  busyId: { type: String, default: '' }
})
const emit = defineEmits(['refresh', 'back', 'decide'])
function fmtTime(value) { return value ? String(value).slice(0, 19).replace('T', ' ') : '—' }
function statusLabel(status) {
  return ({pending:'待审批',approved:'已批准',rejected:'已拒绝',expired:'已过期',failed:'失败'})[status] || status
}
</script>

<template>
  <main class="admin">
    <header><div><span class="eyebrow">APPROVAL CENTER</span><h1>审批中心</h1><p>审核高风险查询。批准前会重新执行安全校验和数据权限检查。</p></div><div class="head-actions"><button class="ghost" @click="emit('refresh')">刷新列表</button><button class="run" @click="emit('back')">← 返回查询</button></div></header>
    <div v-if="error" class="error">{{error}}</div>
    <section class="card">
      <div class="approval-summary"><h3>审批请求（{{approvals.length}}）</h3><span class="muted">仅显示当前租户</span></div>
      <div v-if="!approvals.length" class="approval-empty">暂无审批请求。</div>
      <div v-for="item in approvals" :key="item.id" class="approval-item">
        <div class="approval-main"><div class="approval-title">{{item.question}}</div><div class="approval-meta">{{item.datasource_id}} · {{fmtTime(item.created_at)}} · {{item.id}}</div><pre>{{item.sql}}</pre></div>
        <div class="approval-side"><span class="state" :class="item.status==='pending'?'pending':item.status==='approved'?'ok':'off'">{{statusLabel(item.status)}}</span><div v-if="item.status==='pending'" class="row-actions"><button class="run" :disabled="busyId===item.id" @click="emit('decide', item, 'approve')">批准并执行</button><button class="danger" :disabled="busyId===item.id" @click="emit('decide', item, 'reject')">拒绝</button></div></div>
        <div v-if="item.execution" class="approval-result">执行完成：{{item.execution.row_count}} 行</div>
      </div>
    </section>
  </main>
</template>
