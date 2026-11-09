import {computed, ref, watch} from 'vue'

const emptyDraft=()=>({skill_id:'',version:'',description:'',trigger_terms:'',intents:'',workflow_steps:''})

export function useSkillReleases(api,authFetch){
  const skillCatalog=ref([]);const skillVersions=ref([]);const skillReleaseDraft=ref(emptyDraft())
  const skillCanaryPercent=ref(10);const skillPreviewSubject=ref('');const skillPreviewResult=ref(null)
  const skillReleaseError=ref('');const skillReleaseLoading=ref(false);const skillReleaseBusyId=ref('')
  const skillEvaluationResult=ref(null)
  const skillQueryQualityResult=ref(null)
  const selectedDefinition=computed(()=>skillCatalog.value.find(item=>item.id===skillReleaseDraft.value.skill_id)||null)
  watch(skillReleaseDraft,()=>{skillEvaluationResult.value=null;skillQueryQualityResult.value=null},{deep:true})
  async function request(path,options={}){
    const response=await authFetch(`${api}/api/v1/skills${path}`,options);const data=await response.json().catch(()=>({}))
    if(!response.ok)throw new Error(data.detail?.message||data.detail||'Skill 发布请求失败');return data
  }
  function selectSkill(skillId){
    const definition=skillCatalog.value.find(item=>item.id===skillId)
    skillReleaseDraft.value={skill_id:skillId,version:'',description:definition?.description||'',trigger_terms:(definition?.trigger_terms||[]).join('\n'),intents:(definition?.intents||[]).join('\n'),workflow_steps:(definition?.workflow_steps||[]).join('\n')};skillPreviewResult.value=null
  }
  function definitionFromDraft(){
    const base=selectedDefinition.value;if(!base)return null
    const list=(value)=>value.split(/\r?\n/).map(item=>item.trim()).filter(Boolean)
    const {deployment,...definition}=base
    return{...definition,version:skillReleaseDraft.value.version.trim(),description:skillReleaseDraft.value.description.trim(),trigger_terms:list(skillReleaseDraft.value.trigger_terms),intents:list(skillReleaseDraft.value.intents),workflow_steps:list(skillReleaseDraft.value.workflow_steps)}
  }
  async function loadSkillReleases(){
    skillReleaseLoading.value=true;skillReleaseError.value=''
    try{
      const [catalog,versions]=await Promise.all([request('/admin/catalog'),request('/admin/versions')])
      skillCatalog.value=Array.isArray(catalog)?catalog:[];skillVersions.value=Array.isArray(versions)?versions:[]
      if(!skillReleaseDraft.value.skill_id&&skillCatalog.value.length)selectSkill(skillCatalog.value[0].id)
    }catch(error){skillReleaseError.value=error.message}finally{skillReleaseLoading.value=false}
  }
  async function createSkillDraft(){
    const definition=definitionFromDraft();if(!definition){skillReleaseError.value='请选择目标 Skill';return}
    skillReleaseError.value='';skillReleaseBusyId.value='create'
    try{
      await request('/admin/versions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({definition})})
      skillReleaseDraft.value.version='';await loadSkillReleases()
    }catch(error){skillReleaseError.value=error.message}finally{skillReleaseBusyId.value=''}
  }
  async function evaluateSkillDraft(){
    const definition=definitionFromDraft();if(!definition){skillReleaseError.value='请选择目标 Skill';return}
    skillReleaseError.value='';skillReleaseBusyId.value='evaluate'
    try{skillEvaluationResult.value=await request('/admin/evaluate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({definition})})}
    catch(error){skillReleaseError.value=error.message}finally{skillReleaseBusyId.value=''}
  }
  async function evaluateSkillQueryQuality(){
    const definition=definitionFromDraft();if(!definition){skillReleaseError.value='请选择目标 Skill';return}
    const submittedDefinition=JSON.stringify(definition)
    skillReleaseError.value='';skillReleaseBusyId.value='evaluate-query-quality';skillQueryQualityResult.value=null
    try{
      const report=await request('/admin/evaluate-query-quality',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({definition})})
      if(JSON.stringify(definitionFromDraft())===submittedDefinition)skillQueryQualityResult.value=report
      else skillReleaseError.value='评测期间草稿已变更，旧结果已丢弃，请重新评测'
    }
    catch(error){skillReleaseError.value=error.message}finally{skillReleaseBusyId.value=''}
  }
  async function transition(version,action,body){
    skillReleaseError.value='';skillReleaseBusyId.value=version.id
    try{await request(`/admin/versions/${encodeURIComponent(version.id)}/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});await loadSkillReleases()}
    catch(error){skillReleaseError.value=error.message}finally{skillReleaseBusyId.value=''}
  }
  const startSkillCanary=(version)=>transition(version,'canary',{percentage:Number(skillCanaryPercent.value)})
  const publishSkillVersion=(version)=>transition(version,'publish')
  const rollbackSkillVersion=(version)=>transition(version,'rollback')
  async function previewSkillResolution(){
    if(!skillReleaseDraft.value.skill_id||!skillPreviewSubject.value.trim()){skillReleaseError.value='请选择 Skill 并输入预览用户';return}
    skillReleaseError.value='';skillReleaseBusyId.value='preview'
    try{skillPreviewResult.value=await request('/admin/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({skill_id:skillReleaseDraft.value.skill_id,subject:skillPreviewSubject.value.trim()})})}
    catch(error){skillReleaseError.value=error.message}finally{skillReleaseBusyId.value=''}
  }
  return{skillCatalog,skillVersions,skillReleaseDraft,skillCanaryPercent,skillPreviewSubject,skillPreviewResult,skillEvaluationResult,skillQueryQualityResult,skillReleaseError,skillReleaseLoading,skillReleaseBusyId,loadSkillReleases,selectSkill,createSkillDraft,evaluateSkillDraft,evaluateSkillQueryQuality,startSkillCanary,publishSkillVersion,rollbackSkillVersion,previewSkillResolution}
}
