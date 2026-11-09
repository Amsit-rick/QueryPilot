import { ref } from 'vue'

const emptyEntry = datasourceId => ({ datasource_id: datasourceId || '', kind: 'metric', name: '', description: '', aliases: '', keywords: '', definition: '{}' })

function parseTerms(value) {
  return [...new Set(String(value || '').split(/[\s,，、;；\n]+/).map(item => item.trim()).filter(Boolean))]
}

export function useSemantics(api, authFetch, datasources) {
  const semantics = ref([]); const semanticDatasource = ref(''); const semanticDraft = ref(emptyEntry(''))
  const semanticEditingId = ref(''); const semanticError = ref(''); const semanticLoading = ref(false); const semanticBusy = ref(false)
  const semanticHistory = ref([]); const semanticHistoryId = ref('')

  function ensureDatasource() {
    if (!semanticDatasource.value || !datasources.value.some(item => item.id === semanticDatasource.value)) semanticDatasource.value = datasources.value[0]?.id || ''
  }
  async function loadSemantics() {
    ensureDatasource(); semanticError.value = ''; semantics.value = []
    if (!semanticDatasource.value) return
    semanticLoading.value = true
    try {
      const response = await authFetch(`${api}/api/v1/semantics?datasource_id=${encodeURIComponent(semanticDatasource.value)}`)
      const data = await response.json().catch(() => ([]))
      if (response.ok) semantics.value = Array.isArray(data) ? data : []
      else semanticError.value = data.detail?.message || data.detail || '语义条目加载失败'
    } catch { semanticError.value = '无法连接语义目录服务' }
    finally { semanticLoading.value = false }
  }
  async function selectSemanticDatasource() { cancelSemanticEdit(); await loadSemantics() }
  function startSemanticCreate() { semanticEditingId.value = ''; semanticDraft.value = emptyEntry(semanticDatasource.value) }
  function editSemantic(entry) {
    semanticEditingId.value = entry.id
    const definition = entry.definition || {}
    semanticDraft.value = { datasource_id: entry.datasource_id || semanticDatasource.value, kind: entry.kind, name: entry.name, description: entry.description || '', aliases: Array.isArray(definition.aliases) ? definition.aliases.join('、') : '', keywords: Array.isArray(definition.keywords) ? definition.keywords.join('、') : '', definition: JSON.stringify(definition, null, 2) }
  }
  function cancelSemanticEdit() { startSemanticCreate(); semanticError.value = '' }
  async function saveSemantic() {
    semanticError.value = ''; const name = semanticDraft.value.name.trim()
    if (!name) { semanticError.value = '请输入语义条目名称'; return }
    let definition
    try { definition = JSON.parse(semanticDraft.value.definition); if (!definition || Array.isArray(definition) || typeof definition !== 'object') throw new Error() }
    catch { semanticError.value = '定义必须是有效的 JSON 对象'; return }
    for (const field of ['aliases', 'keywords']) {
      if (semanticDraft.value.kind === 'value_dictionary' && field === 'aliases') { delete definition.aliases; continue }
      const terms = parseTerms(semanticDraft.value[field])
      if (terms.length) definition[field] = terms
      else delete definition[field]
    }
    semanticBusy.value = true
    try {
      const payload = { datasource_id: semanticDatasource.value, kind: semanticDraft.value.kind, name, description: semanticDraft.value.description.trim(), definition }
      const editing = semanticEditingId.value
      const response = await authFetch(editing ? `${api}/api/v1/semantics/${encodeURIComponent(editing)}` : `${api}/api/v1/semantics`, { method: editing ? 'PATCH' : 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) { semanticError.value = data.detail?.message || data.detail || '语义条目保存失败'; return }
      startSemanticCreate(); await loadSemantics()
    } catch { semanticError.value = '无法连接语义目录服务' }
    finally { semanticBusy.value = false }
  }
  async function removeSemantic(entry) {
    if (!window.confirm(`确定删除语义条目“${entry.name}”吗？`)) return
    semanticError.value = ''; semanticBusy.value = true
    try {
      const response = await authFetch(`${api}/api/v1/semantics/${encodeURIComponent(entry.id)}`, {method: 'DELETE'})
      const data = response.ok ? {} : await response.json().catch(() => ({}))
      if (!response.ok) { semanticError.value = data.detail?.message || data.detail || '语义条目删除失败'; return }
      if (semanticEditingId.value === entry.id) startSemanticCreate()
      await loadSemantics()
    } catch { semanticError.value = '无法连接语义目录服务' }
    finally { semanticBusy.value = false }
  }
  async function loadSemanticHistory(entry) {
    semanticHistoryId.value = entry.id; semanticHistory.value = []
    const response = await authFetch(`${api}/api/v1/semantics/${encodeURIComponent(entry.id)}/history`)
    if (response.ok) semanticHistory.value = await response.json()
  }
  return { semantics, semanticDatasource, semanticDraft, semanticEditingId, semanticError, semanticLoading, semanticBusy, semanticHistory, semanticHistoryId, loadSemantics, selectSemanticDatasource, editSemantic, cancelSemanticEdit, saveSemantic, removeSemantic, loadSemanticHistory }
}
