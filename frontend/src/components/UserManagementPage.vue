<script setup>
defineProps({
  users: { type: Array, default: () => [] },
  error: { type: String, default: '' },
  grantUser: { type: Object, default: null },
  grants: { type: Array, default: () => [] },
  grantError: { type: String, default: '' },
  datasources: { type: Array, default: () => [] },
  grantColumns: { type: Array, default: () => [] },
  policyDatasource: { type: String, default: 'default' },
  policyRole: { type: String, default: 'viewer' },
  rolePolicy: { type: Object, default: () => ({tables: [], columns: {}, mask: {}}) },
  policySchema: { type: Object, default: () => ({}) },
  policyError: { type: String, default: '' },
  policyBusy: { type: Boolean, default: false }
})
const newUser = defineModel('newUser', { type: Object, required: true })
const newGrant = defineModel('newGrant', { type: Object, required: true })
const emit = defineEmits(['back', 'create', 'toggle', 'change-role', 'reset-password', 'open-grants', 'remove-user', 'select-datasource', 'add-grant', 'remove-grant', 'select-policy-datasource', 'select-policy-role', 'toggle-policy-table', 'toggle-policy-column', 'set-policy-mask', 'save-policy'])
</script>

<template>
  <main class="admin">
    <header><div><span class="eyebrow">ADMIN CONSOLE</span><h1>用户管理</h1><p>管理当前租户的账号：创建、启停、角色、重置密码与删除。</p></div><button class="run" @click="emit('back')">← 返回查询</button></header>
    <section class="card">
      <h3>新建用户</h3>
      <form class="user-create" @submit.prevent="emit('create')"><input v-model="newUser.username" autocomplete="off" placeholder="用户名"><input v-model="newUser.password" type="password" autocomplete="new-password" minlength="12" placeholder="密码（至少 12 位）"><select v-model="newUser.role"><option value="viewer">viewer</option><option value="analyst">analyst</option><option value="admin">admin</option></select><button class="run" type="submit">创建用户</button></form>
      <div v-if="error" class="error">{{error}}</div>
    </section>
    <section class="card">
      <h3>账号列表（{{users.length}}）</h3>
      <table class="user-table"><thead><tr><th>ID</th><th>用户名</th><th>角色</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody><tr v-for="user in users" :key="user.id"><td>{{user.id}}</td><td>{{user.username}}</td><td><span class="role">{{user.role}}</span></td><td><span :class="user.is_active ? 'state ok' : 'state off'">{{user.is_active ? '启用' : '停用'}}</span></td><td>{{user.created_at ? String(user.created_at).slice(0, 10) : '—'}}</td><td class="row-actions"><button @click="emit('toggle', user)">{{user.is_active ? '停用' : '启用'}}</button><button @click="emit('change-role', user)">角色</button><button @click="emit('reset-password', user)">重置密码</button><button @click="emit('open-grants', user)">数据权限</button><button class="danger" @click="emit('remove-user', user)">删除</button></td></tr></tbody></table>
      <p v-if="!users.length" class="muted">当前租户还没有账号。</p>
    </section>
    <section v-if="grantUser" class="card">
      <h3>数据权限：{{grantUser.username}}</h3><p class="muted">配置行级范围。表级和字段级访问仍由角色白名单控制，以下授权值会自动注入查询过滤条件。</p>
      <form class="user-create" @submit.prevent="emit('add-grant')"><select v-model="newGrant.datasource_id" @change="emit('select-datasource')"><option v-for="ds in datasources" :key="ds.id" :value="ds.id">{{ds.name}}（{{ds.id}}）</option><option v-if="!datasources.length" value="default">default（默认数据源）</option></select><select v-model="newGrant.scope_column"><option v-for="column in grantColumns" :key="column" :value="column">{{column}}</option><option v-if="!grantColumns.length" value="region">region（区域）</option></select><input v-model="newGrant.scope_value" placeholder="授权值，如 华东"><button class="run" type="submit">添加授权</button></form>
      <div v-if="grantError" class="error">{{grantError}}</div>
      <table v-if="grants.length" class="user-table"><thead><tr><th>ID</th><th>数据源</th><th>字段</th><th>值</th><th>操作</th></tr></thead><tbody><tr v-for="grant in grants" :key="grant.id"><td>{{grant.id}}</td><td>{{grant.datasource_id}}</td><td>{{grant.scope_column}}</td><td>{{grant.scope_value}}</td><td class="row-actions"><button class="danger" @click="emit('remove-grant', grant.id)">移除</button></td></tr></tbody></table>
      <p v-else class="muted">该用户暂无数据授权；未配置时按角色权限查询，不施加行级限制。</p>
    </section>
    <section class="card policy-card">
      <h3>角色权限策略</h3>
      <p class="muted">配置角色可访问的数据表和字段。保存后立即用于查询校验；行级范围仍在用户的“数据权限”中配置。</p>
      <div class="policy-toolbar">
        <label>数据源<select :value="policyDatasource" @change="emit('select-policy-datasource', $event.target.value)"><option v-for="ds in datasources" :key="ds.id" :value="ds.id">{{ds.name}}（{{ds.id}}）</option><option v-if="!datasources.length" value="default">default（默认数据源）</option></select></label>
        <label>角色<select :value="policyRole" @change="emit('select-policy-role', $event.target.value)"><option value="viewer">viewer</option><option value="analyst">analyst</option><option value="admin">admin</option></select></label>
      </div>
      <div v-if="policyError" class="error">{{policyError}}</div>
      <div v-if="Object.keys(policySchema).length" class="policy-grid">
        <div v-for="(columns, table) in policySchema" :key="table" class="policy-table">
          <label class="policy-table-title"><input type="checkbox" :checked="rolePolicy.tables === '*' || rolePolicy.tables?.includes(table)" @change="emit('toggle-policy-table', table)"><strong>{{table}}</strong></label>
          <label v-for="column in columns" :key="column" class="policy-column"><input type="checkbox" :checked="rolePolicy.columns === '*' || rolePolicy.columns?.[table]?.includes(column)" @change="emit('toggle-policy-column', table, column)"><code>{{column}}</code><select :value="rolePolicy.mask?.[column] || 'none'" @change="emit('set-policy-mask', column, $event.target.value)"><option value="none">不脱敏</option><option value="mask">完全脱敏</option><option value="partial">部分脱敏</option></select></label>
        </div>
      </div>
      <p v-else class="muted">暂无可配置的 Schema，请先确认数据源连接。</p>
      <button class="run" :disabled="policyBusy" @click="emit('save-policy')">{{policyBusy ? '保存中…' : '保存角色权限'}}</button>
    </section>
  </main>
</template>
