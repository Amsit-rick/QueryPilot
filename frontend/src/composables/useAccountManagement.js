import { ref } from 'vue'

export function useAccountManagement({api, authFetch, logout, queryError}) {
  const sessions = ref([])
  const password = ref({current: '', next: '', confirm: ''})
  const passwordError = ref('')
  const passwordMessage = ref('')

  async function changePassword() {
    const value = password.value
    passwordError.value = ''
    passwordMessage.value = ''
    if (!value.current || !value.next) {
      passwordError.value = '请填写当前密码和新密码'
      return
    }
    if (value.next.length < 12) {
      passwordError.value = '新密码至少 12 位'
      return
    }
    if (value.next !== value.confirm) {
      passwordError.value = '两次输入的新密码不一致'
      return
    }
    const response = await authFetch(`${api}/api/v1/auth/change-password`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({current_password: value.current, new_password: value.next})})
    if (response.ok) {
      passwordMessage.value = '密码已更新，请重新登录'
      password.value = {current: '', next: '', confirm: ''}
      setTimeout(() => logout(false), 1200)
    } else passwordError.value = (await response.json()).detail || '修改密码失败'
  }

  async function loadDevices() {
    const response = await authFetch(`${api}/api/v1/auth/sessions`)
    if (response.ok) sessions.value = await response.json()
    else if (response.status === 401) await logout(false)
  }

  async function revokeDevice(sessionId) {
    if (!window.confirm('确认退出该设备？')) return
    const response = await authFetch(`${api}/api/v1/auth/sessions/${sessionId}/revoke`, {method: 'POST'})
    if (response.ok) await loadDevices()
    else queryError.value = (await response.json()).detail || '退出设备失败'
  }

  async function revokeAllDevices() {
    if (!window.confirm('确认退出全部设备（包括当前设备）？')) return
    const response = await authFetch(`${api}/api/v1/auth/sessions/revoke-all`, {method: 'POST'})
    if (response.ok) await logout(false)
    else queryError.value = (await response.json()).detail || '操作失败'
  }

  return { sessions, password, passwordError, passwordMessage, changePassword, loadDevices, revokeDevice, revokeAllDevices }
}
