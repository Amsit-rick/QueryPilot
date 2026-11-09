import { ref } from 'vue'

function newClientRequestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `query-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

export function useTasks(api, authFetch, requestScope = null) {
  const tasks = ref([])
  const taskDetail = ref(null)
  const taskError = ref('')
  const taskLoading = ref(false)

  async function loadTasks() {
    const scope = requestScope?.capture()
    taskError.value = ''
    try {
      const response = await authFetch(`${api}/api/v1/tasks?limit=100`, {signal: requestScope?.signal()})
      if (requestScope && !requestScope.isCurrent(scope)) return
      if (response.ok) tasks.value = await response.json()
      else taskError.value = (await response.json().catch(() => ({}))).detail || '任务列表加载失败'
    } catch (caught) {
      if (caught?.name !== 'AbortError' && (!requestScope || requestScope.isCurrent(scope))) taskError.value = '无法加载任务，请确认 API 已启动'
    }
  }

  async function loadTask(id) {
    if (!id) return
    const scope = requestScope?.capture()
    taskLoading.value = true
    taskError.value = ''
    taskDetail.value = null
    try {
      const response = await authFetch(`${api}/api/v1/tasks/${encodeURIComponent(id)}`, {signal: requestScope?.signal()})
      if (requestScope && !requestScope.isCurrent(scope)) return
      if (response.ok) taskDetail.value = await response.json()
      else taskError.value = (await response.json().catch(() => ({}))).detail || '任务详情加载失败'
    } catch (caught) {
      if (caught?.name !== 'AbortError' && (!requestScope || requestScope.isCurrent(scope))) taskError.value = '无法加载任务详情，请确认 API 已启动'
    } finally {
      if (!requestScope || requestScope.isCurrent(scope)) taskLoading.value = false
    }
  }

  async function followUpTask(id, question) {
    const scope = requestScope?.capture()
    const response = await authFetch(`${api}/api/v1/query`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        task_id: id,
        follow_up: true,
        question: question.trim(),
        client_request_id: newClientRequestId(),
      }),
      signal: requestScope?.signal()
    })
    if (requestScope && !requestScope.isCurrent(scope)) return null
    const data = await response.json().catch(() => ({}))
    if (!response.ok || data.error) {
      throw new Error(data.error?.message || data.detail?.message || data.detail || '追问失败')
    }
    await loadTask(id)
    return data
  }

  async function submitTaskFeedback(id, payload) {
    const scope = requestScope?.capture()
    const response = await authFetch(`${api}/api/v1/tasks/${encodeURIComponent(id)}/feedback`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
      signal: requestScope?.signal()
    })
    if (requestScope && !requestScope.isCurrent(scope)) return null
    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(data.detail?.message || data.detail || '反馈提交失败')
    }
    await loadTask(id)
    return data
  }

  return { tasks, taskDetail, taskError, taskLoading, loadTasks, loadTask, followUpTask, submitTaskFeedback }
}
