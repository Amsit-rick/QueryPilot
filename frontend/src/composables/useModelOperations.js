import { ref } from 'vue'

const emptyProvider = () => ({name:'', adapter:'direct', base_url:'https://api.deepseek.com', model:'deepseek-chat', api_key_env:'MODEL_API_KEY', enabled:true, is_default:false})
const emptyPrompt = () => ({name:'sql_generation', system_prompt:'', activate:true})

export function useModelOperations(api, authFetch) {
  const modelProviders = ref([]); const promptVersions = ref([]); const modelRuntime = ref(null)
  const providerDraft = ref(emptyProvider()); const providerEditingId = ref(''); const promptDraft = ref(emptyPrompt())
  const modelOpsError = ref(''); const modelOpsLoading = ref(false); const modelOpsBusy = ref(false)
  async function request(path, options = {}) {
    const response = await authFetch(`${api}/api/v1/model-ops${path}`, options)
    const data = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(data.detail?.message || data.detail || '模型运营请求失败')
    return data
  }
  async function loadModelOperations() {
    modelOpsLoading.value = true; modelOpsError.value = ''
    try {
      const [providers, prompts, runtime] = await Promise.all([request('/providers'), request('/prompts?name=sql_generation'), request('/runtime')])
      modelProviders.value = Array.isArray(providers) ? providers : []; promptVersions.value = Array.isArray(prompts) ? prompts : []; modelRuntime.value = runtime
    } catch (error) { modelOpsError.value = error.message }
    finally { modelOpsLoading.value = false }
  }
  function editProvider(provider) { providerEditingId.value = provider.id; providerDraft.value = {name:provider.name,adapter:provider.adapter,base_url:provider.base_url,model:provider.model,api_key_env:provider.api_key_env,enabled:provider.enabled,is_default:provider.is_default} }
  function cancelProviderEdit() { providerEditingId.value = ''; providerDraft.value = emptyProvider() }
  async function saveProvider() {
    modelOpsError.value = ''; modelOpsBusy.value = true
    try { const id=providerEditingId.value; await request(id?`/providers/${encodeURIComponent(id)}`:'/providers',{method:id?'PATCH':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(providerDraft.value)}); cancelProviderEdit(); await loadModelOperations() }
    catch(error){modelOpsError.value=error.message} finally{modelOpsBusy.value=false}
  }
  async function patchProvider(provider, changes) {
    modelOpsError.value=''; modelOpsBusy.value=true
    try{await request(`/providers/${encodeURIComponent(provider.id)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(changes)});await loadModelOperations()}catch(error){modelOpsError.value=error.message}finally{modelOpsBusy.value=false}
  }
  async function checkProviderHealth(provider) {
    modelOpsError.value='';modelOpsBusy.value=true
    try{await request(`/providers/${encodeURIComponent(provider.id)}/health`,{method:'POST'});await loadModelOperations()}catch(error){modelOpsError.value=error.message}finally{modelOpsBusy.value=false}
  }
  async function savePromptVersion() {
    modelOpsError.value='';modelOpsBusy.value=true
    try{await request('/prompts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(promptDraft.value)});promptDraft.value=emptyPrompt();await loadModelOperations()}catch(error){modelOpsError.value=error.message}finally{modelOpsBusy.value=false}
  }
  async function activatePrompt(prompt) {
    modelOpsError.value='';modelOpsBusy.value=true
    try{await request(`/prompts/${encodeURIComponent(prompt.id)}/activate`,{method:'POST'});await loadModelOperations()}catch(error){modelOpsError.value=error.message}finally{modelOpsBusy.value=false}
  }
  return {modelProviders,promptVersions,modelRuntime,providerDraft,providerEditingId,promptDraft,modelOpsError,modelOpsLoading,modelOpsBusy,loadModelOperations,editProvider,cancelProviderEdit,saveProvider,patchProvider,checkProviderHealth,savePromptVersion,activatePrompt}
}
