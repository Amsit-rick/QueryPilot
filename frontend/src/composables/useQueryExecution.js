import { ref } from 'vue'
import { consumeQueryStream, createQueryStreamState, QUERY_STREAM_FALLBACK_STATUSES, QueryStreamInterruptedError, QueryStreamProtocolError } from '../utils/queryStream'

function newClientRequestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `query-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

export function useQueryExecution({api, authFetch, activeDatasource, loadTasks, onUnauthorized, onTaskStarted, requestScope = null}) {
  const question = ref('')
  const sql = ref('')
  const loading = ref(false)
  const error = ref('')
  const result = ref(null)
  const requireApproval = ref(false)
  const approvalNotice = ref(null)
  const exportJob = ref(null)
  const conversationTaskId = ref('')
  const conversationStatus = ref('')
  const conversationTitle = ref('')
  const conversationTurns = ref([])
  const conversationLoading = ref(false)
  const conversationError = ref('')
  const branchDraft = ref(null)
  const branchOrigin = ref(null)
  const branchFamily = ref([])
  const branchFamilyLoading = ref(false)
  let exportPoll = null
  let queryController = null
  let requestSequence = 0
  let activeTurnId = ''

  function responseErrorMessage(response, data, fallback = '查询失败') {
    const detail = data?.detail
    const detailMessage = typeof detail === 'string' ? detail : detail?.message
    const message = data?.message || detailMessage || data?.error?.message
    if (message) return response.ok ? String(message) : `API 请求失败（${response.status}）：${String(message)}`
    return response.ok ? fallback : `API 请求失败（${response.status}）：服务器未返回具体错误信息`
  }

  function onQuestionEdit() {
    if (!loading.value) sql.value = ''
  }

  function beginBranch(turn) {
    if (loading.value || turn?.status !== 'completed' || !turn?.result) return
    const sourceTraceId = String(turn.trace_id || turn.result.trace_id || turn.id || '')
    if (!conversationTaskId.value || !sourceTraceId) return
    branchDraft.value = {
      source_task_id: conversationTaskId.value,
      source_trace_id: sourceTraceId,
      source_question: String(turn.question || '已完成轮次'),
    }
    question.value = ''
    sql.value = ''
    error.value = ''
  }

  function cancelBranchDraft() {
    branchDraft.value = null
    question.value = ''
    sql.value = ''
    error.value = ''
  }

  async function loadBranchFamily(taskId) {
    if (!taskId) {
      branchFamily.value = []
      return
    }
    branchFamilyLoading.value = true
    try {
      const response = await authFetch(`${api}/api/v1/tasks/${encodeURIComponent(taskId)}/branches`, {signal: requestScope?.signal()})
      const data = await response.json().catch(() => ({}))
      if (response.status === 401) await onUnauthorized()
      if (!response.ok) throw new Error('分析分支列表加载失败')
      branchFamily.value = Array.isArray(data.items) ? data.items : []
    } catch (caught) {
      if (caught?.name !== 'AbortError') branchFamily.value = []
    } finally {
      branchFamilyLoading.value = false
    }
  }

  function cancelQuery() {
    if (!queryController) return
    queryController.abort()
    queryController = null
    loading.value = false
    error.value = '查询已取消'
    const turn = conversationTurns.value.find(item => item.id === activeTurnId)
    if (turn) Object.assign(turn, {status: 'failed', error: '查询已取消'})
    activeTurnId = ''
  }

  async function executeQuery(payload, signal, turn) {
    const streamState = createQueryStreamState()
    const onEvent = event => {
      if (event.event === 'snapshot') {
        const snapshot = event.data.state || {}
        if (snapshot.result) {
          const partial = {...snapshot.result, rows: [...(snapshot.rows || [])]}
          result.value = partial
          Object.assign(turn, {id: partial.trace_id || turn.id, result: partial, sql: partial.sql || '', message: snapshot.terminal ? '' : '正在恢复查询结果'})
        } else if (!snapshot.terminal) turn.message = '查询已恢复，正在继续执行'
      } else if (event.event === 'started') turn.message = '查询已提交，正在生成 SQL 和执行计划'
      else if (event.event === 'progress') turn.message = '查询仍在执行，请稍候'
      else if (event.event === 'result') {
        const partial = {...event.data, rows: []}
        result.value = partial
        Object.assign(turn, {id: partial.trace_id || turn.id, result: partial, sql: partial.sql || '', message: '正在接收查询结果'})
      } else if (event.event === 'rows' && result.value) {
        const partial = {...result.value, rows: [...(result.value.rows || []), ...(event.data.rows || [])]}
        result.value = partial
        turn.result = partial
      }
    }

    let response
    for (let attempt = 0; attempt <= 2; attempt += 1) {
      const cursor = attempt > 0 && streamState.lastSequence !== null ? `?after_sequence=${encodeURIComponent(streamState.lastSequence)}` : ''
      response = await authFetch(`${api}/api/v1/query/stream${cursor}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Query-Stream-Version': '2'},
        body: JSON.stringify(payload),
        signal,
      })
      if (attempt === 0 && QUERY_STREAM_FALLBACK_STATUSES.has(response.status)) {
        response = await authFetch(`${api}/api/v1/query`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload), signal})
        return {response, data: await response.json().catch(() => ({}))}
      }
      if (!response.ok) return {response, data: await response.json().catch(() => ({}))}
      try {
        return {response, data: await consumeQueryStream(response, onEvent, {state: streamState})}
      } catch (caught) {
        if (!(caught instanceof QueryStreamInterruptedError) || attempt >= 2) throw caught
        turn.message = '连接中断，正在恢复查询结果'
      }
    }
    throw new QueryStreamProtocolError('查询流恢复失败')
  }

  async function query() {
    if ((!question.value.trim() && !sql.value.trim()) || loading.value) return
    if (!branchDraft.value && conversationTaskId.value && !['completed', 'failed'].includes(conversationStatus.value)) {
      error.value = conversationStatus.value === 'approval_required' ? '当前轮次正在等待审批，审批完成后才能继续追问' : '当前对话尚未完成，暂时不能继续追问'
      return
    }
    const requestId = ++requestSequence
    const scope = requestScope?.capture()
    const submittedQuestion = question.value.trim() || 'SQL查询'
    const submittedSql = sql.value.trim()
    const pendingBranch = branchDraft.value ? {...branchDraft.value} : null
    const hasTask = Boolean(conversationTaskId.value) && !pendingBranch
    const isFollowUp = hasTask && conversationStatus.value === 'completed'
    const turn = {
      id: `local-${requestId}-${Date.now()}`,
      question: submittedQuestion,
      follow_up: isFollowUp,
      status: 'running',
      result: null,
      error: '',
      message: '',
    }
    if (pendingBranch) {
      conversationTurns.value = []
      conversationTaskId.value = ''
      conversationStatus.value = 'running'
      conversationTitle.value = submittedQuestion
      branchOrigin.value = {
        parent_task_id: pendingBranch.source_task_id,
        root_task_id: pendingBranch.source_task_id,
        source_trace_id: pendingBranch.source_trace_id,
        label: submittedQuestion,
        depth: 0,
      }
      branchFamily.value = []
    }
    conversationTurns.value.push(turn)
    activeTurnId = turn.id
    question.value = ''
    queryController = new AbortController()
    loading.value = true
    error.value = ''
    conversationError.value = ''
    approvalNotice.value = null
    result.value = null
    try {
      const payload = {
        question: submittedQuestion,
        require_approval: requireApproval.value,
        client_request_id: newClientRequestId(),
      }
      if (pendingBranch) {
        payload.branch_from_task_id = pendingBranch.source_task_id
        payload.branch_from_trace_id = pendingBranch.source_trace_id
        payload.branch_label = submittedQuestion.slice(0, 120)
      } else if (hasTask) {
        payload.task_id = conversationTaskId.value
        if (isFollowUp) payload.follow_up = true
        else if (activeDatasource.value) payload.datasource = activeDatasource.value
      } else if (activeDatasource.value) payload.datasource = activeDatasource.value
      if (submittedSql) payload.sql = submittedSql
      const {response, data} = await executeQuery(payload, queryController.signal, turn)
      if (requestId !== requestSequence || (requestScope && !requestScope.isCurrent(scope))) return
      if (response.status === 401) await onUnauthorized()
      else if (data.status === 'approval_required') {
        approvalNotice.value = data
        sql.value = data.sql || sql.value
        conversationTaskId.value = data.task_id || conversationTaskId.value
        conversationStatus.value = 'approval_required'
        conversationTitle.value ||= submittedQuestion
        branchOrigin.value = data.branch_origin || branchOrigin.value
        Object.assign(turn, {id: data.trace_id || turn.id, status: 'approval_required', message: data.message, approvalNotice: data})
        onTaskStarted?.(conversationTaskId.value)
        await loadTasks()
        await loadBranchFamily(conversationTaskId.value)
        if (conversationTaskId.value) branchDraft.value = null
      } else if (data.error) {
        error.value = data.error.message
        Object.assign(turn, {id: data.trace_id || turn.id, status: 'failed', error: data.error.message, sql: data.sql || ''})
        conversationTaskId.value = data.task_id || conversationTaskId.value
        conversationStatus.value = isFollowUp ? 'completed' : 'failed'
        conversationTitle.value ||= submittedQuestion
        branchOrigin.value = data.branch_origin || branchOrigin.value
        onTaskStarted?.(conversationTaskId.value)
        await loadTasks()
        if (conversationTaskId.value) {
          await loadBranchFamily(conversationTaskId.value)
          branchDraft.value = null
        }
      } else if (!response.ok) {
        const message = responseErrorMessage(response, data)
        error.value = message
        Object.assign(turn, {status: 'failed', error: message})
      }
      else {
        result.value = data
        sql.value = data.sql
        conversationTaskId.value = data.task_id || conversationTaskId.value
        conversationStatus.value = 'completed'
        conversationTitle.value ||= submittedQuestion
        branchOrigin.value = data.branch_origin || branchOrigin.value
        Object.assign(turn, {id: data.trace_id || turn.id, status: 'completed', result: data, sql: data.sql || ''})
        onTaskStarted?.(conversationTaskId.value)
        await loadTasks()
        await loadBranchFamily(conversationTaskId.value)
        branchDraft.value = null
      }
    } catch (caught) {
      if ((!requestScope || requestScope.isCurrent(scope)) && caught?.name !== 'AbortError') {
        error.value = caught instanceof QueryStreamProtocolError ? caught.message : '无法连接 API，请确认后端已启动'
        Object.assign(turn, {status: 'failed', error: error.value})
      }
    } finally {
      if (requestId === requestSequence && (!requestScope || requestScope.isCurrent(scope))) {
        loading.value = false
        queryController = null
        activeTurnId = ''
      }
    }
  }

  async function resumeConversation(taskId, {force = false} = {}) {
    if (!taskId || (!force && conversationTaskId.value === taskId && conversationTurns.value.length)) return
    const scope = requestScope?.capture()
    conversationLoading.value = true
    conversationError.value = ''
    try {
      const response = await authFetch(`${api}/api/v1/tasks/${encodeURIComponent(taskId)}/conversation`, {signal: requestScope?.signal()})
      const data = await response.json().catch(() => ({}))
      if (requestScope && !requestScope.isCurrent(scope)) return
      if (!response.ok) throw new Error(data.detail?.message || data.detail || '对话加载失败')
      conversationTaskId.value = data.task_id
      conversationStatus.value = data.status
      conversationTitle.value = data.title || '数据对话'
      activeDatasource.value = data.datasource_id || activeDatasource.value
      conversationTurns.value = (data.turns || []).map(item => ({...item}))
      branchDraft.value = null
      branchOrigin.value = data.branch_origin || null
      const latestResult = [...conversationTurns.value].reverse().find(item => item.result)?.result || null
      const latestApproval = [...conversationTurns.value].reverse().find(item => item.status === 'approval_required') || null
      result.value = latestResult
      sql.value = latestResult?.sql || latestApproval?.sql || ''
      approvalNotice.value = latestApproval ? {message: latestApproval.message, approval_id: latestApproval.approval_id} : null
      error.value = ''
      question.value = ''
      await loadBranchFamily(data.task_id)
    } catch (caught) {
      if (caught?.name !== 'AbortError' && (!requestScope || requestScope.isCurrent(scope))) conversationError.value = caught?.message || '对话加载失败'
    } finally {
      if (!requestScope || requestScope.isCurrent(scope)) conversationLoading.value = false
    }
  }

  function reset() {
    requestSequence += 1
    queryController?.abort()
    queryController = null
    question.value = ''
    sql.value = ''
    result.value = null
    error.value = ''
    approvalNotice.value = null
    requireApproval.value = false
    exportJob.value = null
    conversationTaskId.value = ''
    conversationStatus.value = ''
    conversationTitle.value = ''
    conversationTurns.value = []
    conversationLoading.value = false
    conversationError.value = ''
    branchDraft.value = null
    branchOrigin.value = null
    branchFamily.value = []
    branchFamilyLoading.value = false
    activeTurnId = ''
    if (exportPoll) clearTimeout(exportPoll)
  }

  function usePrompt(prompt) {
    question.value = prompt
    sql.value = ''
    error.value = ''
  }

  async function startExport() {
    if (!result.value?.sql && !sql.value.trim()) return
    const scope = requestScope?.capture()
    const body = {question: result.value?.question || conversationTitle.value || 'SQL查询', sql: result.value?.sql || sql.value.trim()}
    exportJob.value = {status: 'queued'}
    try {
      const response = await authFetch(`${api}/api/v1/export`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal: requestScope?.signal()})
      const data = await response.json().catch(() => ({}))
      if (requestScope && !requestScope.isCurrent(scope)) return
      if (!response.ok) {
        exportJob.value = {status: 'failed', error: data.detail || '导出失败，请重新登录后重试'}
        return
      }
      exportJob.value = {job_id: data.job_id, status: data.status}
      pollExport(data.job_id, scope)
    } catch (caught) {
      if (caught?.name !== 'AbortError' && (!requestScope || requestScope.isCurrent(scope))) exportJob.value = {status: 'failed', error: '无法连接 API，请确认后端已启动'}
    }
  }

  async function pollExport(jobId, scope = requestScope?.capture()) {
    if (exportPoll) clearTimeout(exportPoll)
    try {
      const response = await authFetch(`${api}/api/v1/export/${jobId}`, {signal: requestScope?.signal()})
      if (requestScope && !requestScope.isCurrent(scope)) return
      if (!response.ok) {
        exportJob.value = {job_id: jobId, status: 'failed', error: '导出状态查询失败'}
        return
      }
      const data = await response.json()
      exportJob.value = {...data, job_id: jobId}
      if (['queued', 'running'].includes(data.status)) exportPoll = setTimeout(() => pollExport(jobId, scope), 1000)
    } catch (caught) {
      if (caught?.name !== 'AbortError' && (!requestScope || requestScope.isCurrent(scope))) exportJob.value = {job_id: jobId, status: 'failed', error: '无法连接 API，请确认后端已启动'}
    }
  }

  async function downloadExport(job) {
    if (!job?.job_id) return
    const scope = requestScope?.capture()
    try {
      const response = await authFetch(`${api}/api/v1/export/${job.job_id}/download`, {signal: requestScope?.signal()})
      if (requestScope && !requestScope.isCurrent(scope)) return
      if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        exportJob.value = {...job, status: 'failed', error: data.detail || '下载失败，请重新登录后重试'}
        return
      }
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url
      link.download = `query-${job.job_id}.csv`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch (caught) {
      if (caught?.name !== 'AbortError' && (!requestScope || requestScope.isCurrent(scope))) exportJob.value = {...job, status: 'failed', error: '无法连接 API，请确认后端已启动'}
    }
  }

  return { question, sql, loading, error, result, requireApproval, approvalNotice, exportJob, conversationTaskId, conversationStatus, conversationTitle, conversationTurns, conversationLoading, conversationError, branchDraft, branchOrigin, branchFamily, branchFamilyLoading, onQuestionEdit, beginBranch, cancelBranchDraft, loadBranchFamily, cancelQuery, query, resumeConversation, reset, usePrompt, startExport, downloadExport }
}
