<script setup>
defineProps({ sessions: { type: Array, default: () => [] } })
const emit = defineEmits(['back', 'revoke', 'revoke-all'])
function fmtTime(value) { return value ? String(value).slice(0, 19).replace('T', ' ') : '—' }
function deviceLabel(userAgent) { const value=(userAgent || '').toLowerCase(); if(value.includes('edg')) return 'Edge'; if(value.includes('chrome')) return 'Chrome'; if(value.includes('firefox')) return 'Firefox'; if(value.includes('safari')) return 'Safari'; return '浏览器' }
</script>

<template>
  <main class="admin">
    <header><div><span class="eyebrow">SESSIONS</span><h1>登录会话</h1><p>查看当前账号的登录设备，并可退出单个或全部设备。</p></div><div class="head-actions"><button class="run" @click="emit('back')">← 返回查询</button><button class="ghost danger" @click="emit('revoke-all')">退出全部设备</button></div></header>
    <section class="card"><h3>登录设备（{{sessions.length}}）</h3><table class="user-table"><thead><tr><th>设备</th><th>IP</th><th>登录时间</th><th>最近活动</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="session in sessions" :key="session.id"><td><span class="device">{{deviceLabel(session.user_agent)}}</span><div class="ua">{{session.user_agent || '—'}}</div></td><td>{{session.ip_address || '—'}}</td><td>{{fmtTime(session.created_at)}}</td><td>{{fmtTime(session.last_active_at)}}</td><td><span :class="session.current ? 'state ok' : 'state'">{{session.current ? '当前设备' : '其他设备'}}</span></td><td class="row-actions"><button class="danger" @click="emit('revoke', session.id)">退出</button></td></tr></tbody></table><p v-if="!sessions.length" class="muted">暂无活跃会话。</p></section>
  </main>
</template>
