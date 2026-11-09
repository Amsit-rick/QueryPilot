<script setup>
defineProps({ profile: { type: Object, default: null }, profileError: { type: String, default: '' }, profileMessage: { type: String, default: '' }, error: { type: String, default: '' }, message: { type: String, default: '' } })
const password = defineModel('password', { type: Object, required: true })
const displayName = defineModel('displayName', { type: String, default: '' })
const emit = defineEmits(['back', 'change-password', 'update-profile'])
function fmtTime(value) { return value ? String(value).slice(0, 19).replace('T', ' ') : '—' }
</script>

<template>
  <main class="admin">
    <header><div><span class="eyebrow">ACCOUNT</span><h1>个人资料</h1><p>查看账号信息并修改密码。</p></div><button class="run" @click="emit('back')">← 返回查询</button></header>
    <section class="card"><h3>账号信息</h3><dl class="profile-grid"><div><dt>用户名</dt><dd>{{profile?.username || '—'}}</dd></div><div><dt>角色</dt><dd>{{profile?.role || (profile?.roles || []).join(' / ') || '—'}}</dd></div><div><dt>租户</dt><dd>{{profile?.tenant_id || '—'}}</dd></div><div><dt>账号状态</dt><dd>{{profile?.is_active ? '正常' : '已停用'}}</dd></div><div><dt>注册时间</dt><dd>{{fmtTime(profile?.created_at)}}</dd></div></dl><form class="profile-edit" @submit.prevent="emit('update-profile')"><label>显示名称<input v-model="displayName" maxlength="128" placeholder="用于页面展示"></label><button class="run" type="submit">保存显示名称</button></form><div v-if="profileError" class="error">{{profileError}}</div><div v-if="profileMessage" class="success">{{profileMessage}}</div></section>
    <section class="card"><h3>修改密码</h3><form class="pwd-form" @submit.prevent="emit('change-password')"><label>当前密码<input v-model="password.current" type="password" autocomplete="current-password"></label><label>新密码（至少 12 位）<input v-model="password.next" type="password" autocomplete="new-password"></label><label>确认新密码<input v-model="password.confirm" type="password" autocomplete="new-password"></label><button class="run" type="submit">修改密码</button></form><div v-if="error" class="error">{{error}}</div><div v-if="message" class="success">{{message}}</div></section>
  </main>
</template>
