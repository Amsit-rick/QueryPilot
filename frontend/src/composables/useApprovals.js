import { ref } from 'vue'

export function useApprovals(api, authFetch) {
  const approvals = ref([])
  const approvalError = ref('')
  const approvalBusy = ref('')

  async function loadApprovals() {
    approvalError.value = ''
    const response = await authFetch(`${api}/api/v1/approvals`)
    if (response.ok) approvals.value = await response.json()
    else approvalError.value = (await response.json().catch(() => ({}))).detail || '审批列表加载失败'
  }

  async function decideApproval(item, action) {
    if (approvalBusy.value) return
    approvalBusy.value = item.id
    approvalError.value = ''
    try {
      const response = await authFetch(`${api}/api/v1/approvals/${item.id}/${action}`, {method: 'POST'})
      const data = await response.json().catch(() => ({}))
      if (!response.ok) {
        approvalError.value = data.detail?.message || data.detail || '审批操作失败'
        return
      }
      item.status = data.status
      if (action === 'approve') item.execution = data
    } finally {
      approvalBusy.value = ''
    }
  }

  return { approvals, approvalError, approvalBusy, loadApprovals, decideApproval }
}
