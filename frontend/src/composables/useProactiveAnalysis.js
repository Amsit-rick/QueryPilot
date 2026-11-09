import {ref, watch} from 'vue'

const emptyDraft=()=>({question:'',datasource_id:'',interval_minutes:60,max_runs_per_day:6,daily_budget_usd:0.5,require_approval:true})

export function useProactiveAnalysis(api,authFetch,datasources){
  const proactiveStatus=ref(null)
  const proactivePlans=ref([])
  const proactiveRuns=ref({})
  const proactiveDraft=ref(emptyDraft())
  const proactiveError=ref('')
  const proactiveLoading=ref(false)
  const proactiveBusyId=ref('')

  watch(datasources,values=>{
    if(!proactiveDraft.value.datasource_id&&values?.length)proactiveDraft.value.datasource_id=values[0].id
  },{immediate:true})

  async function request(path,options={}){
    const response=await authFetch(`${api}/api/v1/proactive-analysis${path}`,options)
    const data=await response.json().catch(()=>({}))
    if(!response.ok)throw new Error(data.detail?.message||data.detail||'主动分析请求失败')
    return data
  }

  async function loadProactiveAnalysis(){
    proactiveLoading.value=true;proactiveError.value=''
    try{
      const [status,plans]=await Promise.all([request('/status'),request('/plans')])
      proactiveStatus.value=status
      proactivePlans.value=Array.isArray(plans)?plans:[]
    }catch(error){proactiveError.value=error.message}
    finally{proactiveLoading.value=false}
  }

  async function createProactivePlan(){
    const draft=proactiveDraft.value
    if(!draft.question.trim()||!draft.datasource_id){proactiveError.value='请填写分析问题并选择数据源';return}
    proactiveBusyId.value='create';proactiveError.value=''
    try{
      const created=await request('/plans',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
        question:draft.question.trim(),datasource_id:draft.datasource_id,interval_minutes:Number(draft.interval_minutes),max_runs_per_day:Number(draft.max_runs_per_day),daily_budget_usd:Number(draft.daily_budget_usd),require_approval:Boolean(draft.require_approval),
      })})
      proactivePlans.value=[created,...proactivePlans.value]
      proactiveDraft.value={...emptyDraft(),datasource_id:draft.datasource_id}
    }catch(error){proactiveError.value=error.message}
    finally{proactiveBusyId.value=''}
  }

  async function transitionProactivePlan(plan,action){
    proactiveBusyId.value=plan.id;proactiveError.value=''
    try{
      const updated=await request(`/plans/${encodeURIComponent(plan.id)}/${action}`,{method:'POST'})
      proactivePlans.value=proactivePlans.value.map(item=>item.id===updated.id?updated:item)
    }catch(error){proactiveError.value=error.message}
    finally{proactiveBusyId.value=''}
  }

  async function loadProactiveRuns(plan){
    proactiveBusyId.value=`runs:${plan.id}`;proactiveError.value=''
    try{proactiveRuns.value={...proactiveRuns.value,[plan.id]:await request(`/plans/${encodeURIComponent(plan.id)}/runs`)}}
    catch(error){proactiveError.value=error.message}
    finally{proactiveBusyId.value=''}
  }

  return{proactiveStatus,proactivePlans,proactiveRuns,proactiveDraft,proactiveError,proactiveLoading,proactiveBusyId,loadProactiveAnalysis,createProactivePlan,transitionProactivePlan,loadProactiveRuns}
}
