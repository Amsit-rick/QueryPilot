import { ref } from 'vue'

export function useAuthentication({api, token, authFetch, csrfToken, refreshAccess, router, onAuthenticated, onClear}) {
  const username = ref('')
  const password = ref('')
  const profile = ref(null)
  const displayName = ref('')
  const loginError = ref('')
  const loginLoading = ref(false)
  const profileError = ref('')
  const profileMsg = ref('')

  async function loadIdentity() {
    if (!token.value) return false
    try {
      const response = await authFetch(`${api}/api/v1/auth/me`)
      if (response.status === 401) {
        await logout(false)
        return false
      }
      if (!response.ok) return false
      profile.value = await response.json()
      displayName.value = profile.value.display_name || ''
      await onAuthenticated(profile.value)
      return true
    } catch {
      return false
    }
  }

  async function initialize() {
    if (!token.value && await refreshAccess()) await loadIdentity()
  }

  async function login() {
    loginError.value = ''
    loginLoading.value = true
    try {
      const response = await fetch(`${api}/api/v1/auth/login`, {method: 'POST', credentials: 'include', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username: username.value, password: password.value})})
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || `登录失败（${response.status}）`)
      if (!data.access_token) throw new Error('登录响应缺少访问令牌')
      token.value = data.access_token
      password.value = ''
      await loadIdentity()
    } catch (error) {
      loginError.value = error.message || '无法连接 API，请确认后端已启动'
    } finally {
      loginLoading.value = false
    }
  }

  async function logout(revoke = true) {
    if (revoke) {
      try {
        await fetch(`${api}/api/v1/auth/logout`, {method: 'POST', credentials: 'include', headers: {'X-CSRF-Token': csrfToken()}})
      } catch {}
    }
    token.value = ''
    profile.value = null
    displayName.value = ''
    username.value = ''
    password.value = ''
    localStorage.removeItem('query-pilot-token')
    localStorage.removeItem('query-pilot-refresh')
    onClear()
    await router.replace('/workspace')
  }

  async function updateProfile() {
    profileError.value = ''; profileMsg.value = ''
    try {
      const response = await authFetch(`${api}/api/v1/auth/profile`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({display_name: displayName.value})})
      if (!response.ok) { profileError.value = (await response.json()).detail || '保存资料失败'; return null }
      const data = await response.json()
      profile.value = {...profile.value, display_name: data.display_name}; displayName.value = data.display_name; profileMsg.value = '显示名称已更新'; return data
    } catch { profileError.value = '保存资料失败'; return null }
  }

  return { username, password, profile, displayName, profileError, profileMsg, loginError, loginLoading, initialize, login, logout, loadIdentity, updateProfile }
}
