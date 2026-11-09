import { computed, ref } from 'vue'

export const MIN_PASSWORD_LENGTH = 12

export function useUserManagement({api, authFetch, datasources, schema, loadDatasources, browseDatasource}) {
  const users = ref([])
  const newUser = ref({username: '', password: '', role: 'viewer'})
  const userError = ref('')
  const grantUser = ref(null)
  const grants = ref([])
  const newGrant = ref({datasource_id: 'default', scope_column: 'region', scope_value: ''})
  const grantError = ref('')
  const policyDatasource = ref('default')
  const policyRole = ref('viewer')
  const rolePolicy = ref({tables: [], columns: {}, mask: {}})
  const policySchema = computed(() => schema.value[policyDatasource.value] || {})
  const policyError = ref('')
  const policyBusy = ref(false)
  const grantColumns = computed(() => {
    const columns = Object.values(schema.value[newGrant.value.datasource_id] || {}).flat()
    return [...new Set(columns)].sort()
  })

  async function loadUsers() {
    const response = await authFetch(`${api}/api/v1/admin/users`)
    if (response.ok) users.value = await response.json()
  }

  async function createUser() {
    userError.value = ''
    if (!newUser.value.username || newUser.value.password.length < MIN_PASSWORD_LENGTH) {
      userError.value = `用户名不能为空，密码至少 ${MIN_PASSWORD_LENGTH} 位`
      return
    }
    const response = await authFetch(`${api}/api/v1/admin/users`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(newUser.value)})
    if (!response.ok) {
      userError.value = (await response.json()).detail || '创建失败'
      return
    }
    newUser.value = {username: '', password: '', role: 'viewer'}
    await loadUsers()
  }

  async function toggleUser(user) {
    await authFetch(`${api}/api/v1/admin/users/${user.id}/status`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({active: !user.is_active})})
    await loadUsers()
  }

  async function changeRole(user) {
    const role = window.prompt('角色 viewer / analyst / admin', user.role)
    if (!role || !['viewer', 'analyst', 'admin'].includes(role)) return
    await authFetch(`${api}/api/v1/admin/users/${user.id}/role`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({role})})
    await loadUsers()
  }

  async function removeUser(user) {
    if (!window.confirm(`确认删除 ${user.username}？`)) return
    const response = await authFetch(`${api}/api/v1/admin/users/${user.id}`, {method: 'DELETE'})
    if (!response.ok) userError.value = (await response.json()).detail || '删除失败'
    await loadUsers()
  }

  async function resetUserPassword(user) {
    const password = window.prompt(`为 ${user.username} 设置新密码（至少 ${MIN_PASSWORD_LENGTH} 位）`)
    if (!password) return
    if (password.length < MIN_PASSWORD_LENGTH) {
      userError.value = `新密码至少 ${MIN_PASSWORD_LENGTH} 位`
      return
    }
    const response = await authFetch(`${api}/api/v1/admin/users/${user.id}/reset-password`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({new_password: password})})
    if (!response.ok) userError.value = (await response.json()).detail || '重置密码失败'
    await loadUsers()
  }

  async function loadGrants(userId) {
    const response = await authFetch(`${api}/api/v1/admin/users/${userId}/grants`)
    if (response.ok) grants.value = await response.json()
    else grantError.value = (await response.json()).detail || '加载失败'
  }

  async function openGrants(user) {
    grantUser.value = user
    grantError.value = ''
    await loadDatasources()
    const first = datasources.value[0]?.id || 'default'
    if (!datasources.value.some(item => item.id === newGrant.value.datasource_id)) newGrant.value.datasource_id = first
    await browseDatasource(newGrant.value.datasource_id)
    if (!grantColumns.value.includes(newGrant.value.scope_column)) newGrant.value.scope_column = grantColumns.value[0] || 'region'
    await loadGrants(user.id)
  }

  async function selectGrantDatasource() {
    await browseDatasource(newGrant.value.datasource_id)
    if (!grantColumns.value.includes(newGrant.value.scope_column)) newGrant.value.scope_column = grantColumns.value[0] || 'region'
  }

  async function addGrant() {
    if (!grantUser.value) return
    if (!newGrant.value.scope_value.trim()) {
      grantError.value = '请填写授权值'
      return
    }
    const response = await authFetch(`${api}/api/v1/admin/users/${grantUser.value.id}/grants`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(newGrant.value)})
    if (response.ok) {
      newGrant.value = {datasource_id: 'default', scope_column: 'region', scope_value: ''}
      await loadGrants(grantUser.value.id)
    } else grantError.value = (await response.json()).detail || '添加失败'
  }

  async function removeGrant(grantId) {
    if (!window.confirm('确认移除该数据权限？')) return
    const response = await authFetch(`${api}/api/v1/admin/grants/${grantId}`, {method: 'DELETE'})
    if (response.ok) await loadGrants(grantUser.value.id)
    else grantError.value = (await response.json()).detail || '移除失败'
  }

  async function loadRolePolicy() {
    policyError.value = ''
    await browseDatasource(policyDatasource.value)
    const response = await authFetch(`${api}/api/v1/admin/role-policies?datasource_id=${encodeURIComponent(policyDatasource.value)}&role=${encodeURIComponent(policyRole.value)}`)
    if (response.ok) rolePolicy.value = await response.json()
    else policyError.value = (await response.json()).detail || '加载角色权限失败'
  }

  function selectPolicyDatasource(value) { policyDatasource.value = value; loadRolePolicy() }
  function selectPolicyRole(value) { policyRole.value = value; loadRolePolicy() }
  function togglePolicyTable(table) {
    const current = rolePolicy.value.tables
    rolePolicy.value = {...rolePolicy.value, tables: current === '*' ? [table] : current.includes(table) ? current.filter(item => item !== table) : [...current, table]}
    if (rolePolicy.value.tables.length === 0) rolePolicy.value = {...rolePolicy.value, tables: []}
  }
  function togglePolicyColumn(table, column) {
    const columns = {...(rolePolicy.value.columns === '*' ? {} : rolePolicy.value.columns)}
    const selected = new Set(columns[table] || [])
    selected.has(column) ? selected.delete(column) : selected.add(column)
    columns[table] = [...selected]
    rolePolicy.value = {...rolePolicy.value, columns}
  }
  function setPolicyMask(column, mode) {
    const mask = {...(rolePolicy.value.mask || {})}
    if (mode === 'none') delete mask[column]
    else mask[column] = mode
    rolePolicy.value = {...rolePolicy.value, mask}
  }
  async function saveRolePolicy() {
    policyBusy.value = true; policyError.value = ''
    try {
      const response = await authFetch(`${api}/api/v1/admin/role-policies`, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({datasource_id: policyDatasource.value, role: policyRole.value, tables: rolePolicy.value.tables, columns: rolePolicy.value.columns, mask: rolePolicy.value.mask || {}})})
      if (!response.ok) policyError.value = (await response.json()).detail || '保存角色权限失败'
      else rolePolicy.value = await response.json()
    } finally { policyBusy.value = false }
  }

  return { users, newUser, userError, grantUser, grants, newGrant, grantError, grantColumns, policyDatasource, policyRole, rolePolicy, policySchema, policyError, policyBusy, loadUsers, createUser, toggleUser, changeRole, removeUser, resetUserPassword, openGrants, selectGrantDatasource, addGrant, removeGrant, loadRolePolicy, selectPolicyDatasource, selectPolicyRole, togglePolicyTable, togglePolicyColumn, setPolicyMask, saveRolePolicy }
}
