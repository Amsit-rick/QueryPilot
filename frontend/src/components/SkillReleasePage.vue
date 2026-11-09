<script setup>
defineProps({catalog:{type:Array,default:()=>[]},versions:{type:Array,default:()=>[]},draft:{type:Object,required:true},canaryPercent:{type:Number,default:10},previewSubject:{type:String,default:''},previewResult:{type:Object,default:null},evaluationResult:{type:Object,default:null},queryQualityResult:{type:Object,default:null},error:{type:String,default:''},loading:Boolean,busyId:{type:String,default:''}})
const emit=defineEmits(['refresh','back','select-skill','update:canary-percent','update:preview-subject','create','evaluate','evaluate-query-quality','canary','publish','rollback','preview'])
const stateLabel={draft:'草稿',canary:'灰度',active:'当前生效',retired:'历史版本'}
const caseState={passed:'结果匹配',rejected:'护栏拒绝',result_mismatch:'结果不匹配',failed:'生成或执行失败'}
const caseCategory={aggregation:'分组聚合',sorting_limit:'排序与限制',relational_join:'多表关联',left_join:'左连接与空维度',date_filter:'日期范围',join_fanout:'JOIN 扇出',status_filter:'状态过滤'}
function fmt(value){return value?String(value).slice(0,19).replace('T',' '):'—'}
function pct(value){return value===null||value===undefined?'—':`${Math.round(value*100)}%`}
</script>
<template>
  <main class="admin skill-release-page">
    <header><div><span class="eyebrow">SKILL RELEASES</span><h1>Skill 发布治理</h1><p>声明式版本、稳定灰度和可审计回滚。运行中的请求固定使用已解析版本。</p></div><div class="head-actions"><button class="ghost" :disabled="loading" @click="emit('refresh')">刷新</button><button class="run" @click="emit('back')">← 返回查询</button></div></header>
    <div v-if="error" class="error">{{error}}</div>
    <section class="skill-release-summary">
      <div><span>可发布 Skill</span><b>{{catalog.length}}</b></div><div><span>版本记录</span><b>{{versions.length}}</b></div><div><span>生效版本</span><b>{{versions.filter(item=>item.state==='active').length}}</b></div><div><span>灰度版本</span><b>{{versions.filter(item=>item.state==='canary').length}}</b></div>
    </section>
    <div class="skill-release-grid">
      <section class="card skill-draft-card"><h3>创建版本草稿</h3><form class="skill-release-form" @submit.prevent="emit('create')">
        <label>目标 Skill<select aria-label="目标 Skill" :value="draft.skill_id" required @change="emit('select-skill',$event.target.value)"><option v-for="skill in catalog" :key="skill.id" :value="skill.id">{{skill.name}} · {{skill.version}}</option></select></label>
        <label>新版本号<input v-model="draft.version" aria-label="新版本号" placeholder="1.1.0" pattern="\d+\.\d+\.\d+" required></label>
        <label>版本说明<textarea v-model="draft.description" aria-label="版本说明" maxlength="300" rows="3" required></textarea></label>
        <label>触发词（每行一个）<textarea v-model="draft.trigger_terms" aria-label="触发词" maxlength="1200" rows="3" required></textarea></label>
        <label>适用意图（每行一个）<textarea v-model="draft.intents" aria-label="适用意图" maxlength="600" rows="2" required></textarea></label>
        <label>工作步骤（每行一个）<textarea v-model="draft.workflow_steps" aria-label="工作步骤" maxlength="1200" rows="3" required></textarea></label>
        <p class="skill-boundary">工具、角色和只读限制由系统策略固定，不在此页面编辑。</p>
        <div class="skill-draft-actions"><button class="ghost" type="button" :disabled="busyId==='evaluate'||busyId==='evaluate-query-quality'||!draft.version" @click="emit('evaluate')">{{busyId==='evaluate'?'评测中…':'对比路由效果'}}</button><button class="ghost" type="button" :disabled="busyId==='evaluate'||busyId==='evaluate-query-quality'||!draft.version" @click="emit('evaluate-query-quality')">{{busyId==='evaluate-query-quality'?'生成并校验中…':'评测 SQL 效果'}}</button><button class="run" type="submit" :disabled="busyId==='create'">{{busyId==='create'?'创建中…':'创建草稿'}}</button></div>
        <p class="skill-eval-notice">SQL 效果评测会使用当前已配置模型；固定用例会分别对基线与候选版本生成一次 SQL。SQL 只在隔离的合成 SQLite 库中只读执行，不访问业务数据。没有模型 API Key 时评测会提示配置错误。</p>
        <div v-if="evaluationResult" class="skill-evaluation-result" role="status">
          <strong>固定集路由评测</strong><span>命中率 {{Math.round(evaluationResult.summary.baseline_match_rate*100)}}% → {{Math.round(evaluationResult.summary.candidate_match_rate*100)}}%</span><span>路由变化 {{evaluationResult.summary.route_changes}} 例 · 版本变化 {{evaluationResult.summary.version_changes}} 例</span><small>仅验证 Skill 路由，不代表 SQL 或业务结果准确率。</small>
          <ul><li v-for="item in evaluationResult.cases.filter(row=>row.route_changed)" :key="item.id">{{item.id}}：{{item.baseline.skill_id||'未命中'}} → {{item.candidate.skill_id||'未命中'}}</li></ul>
        </div>
        <div v-if="queryQualityResult" class="skill-query-quality-result" role="status">
          <div class="skill-query-quality-head"><strong>SQL 与结果质量对照 · {{queryQualityResult.summary.cases}} 条用例</strong><span>{{queryQualityResult.dataset_version}} · {{queryQualityResult.database}}</span></div>
          <p>模型 {{queryQualityResult.model}} · Prompt {{queryQualityResult.prompt_version}} · 生成尝试 {{queryQualityResult.generation_attempts}} 次</p>
          <div class="skill-quality-metrics">
            <div><b>基线版本</b><span>生成 {{pct(queryQualityResult.summary.baseline.generation_success_rate)}}</span><span>SQL 合法 {{pct(queryQualityResult.summary.baseline.sql_valid_rate)}}</span><span>护栏通过 {{pct(queryQualityResult.summary.baseline.guardrail_pass_rate)}}</span><span>执行成功 {{pct(queryQualityResult.summary.baseline.execution_success_rate)}}</span><span>结果匹配 {{pct(queryQualityResult.summary.baseline.result_exact_match_rate)}}</span></div>
            <div><b>候选版本 {{draft.version}}</b><span>生成 {{pct(queryQualityResult.summary.candidate.generation_success_rate)}}</span><span>SQL 合法 {{pct(queryQualityResult.summary.candidate.sql_valid_rate)}}</span><span>护栏通过 {{pct(queryQualityResult.summary.candidate.guardrail_pass_rate)}}</span><span>执行成功 {{pct(queryQualityResult.summary.candidate.execution_success_rate)}}</span><span>结果匹配 {{pct(queryQualityResult.summary.candidate.result_exact_match_rate)}}</span></div>
          </div>
          <div class="skill-quality-cases"><div class="skill-quality-case skill-quality-case-head"><span>用例</span><span>基线</span><span>候选</span></div><div v-for="item in queryQualityResult.cases" :key="item.id" class="skill-quality-case"><strong>{{item.id}}<small>{{caseCategory[item.category]||item.category}}</small></strong><span>{{caseState[item.baseline.status]||item.baseline.status}}</span><span>{{caseState[item.candidate.status]||item.candidate.status}}</span></div></div>
          <small>该结果只表示固定合成数据集上的 SQL 生成、护栏、执行及精确匹配情况，不代表真实业务准确率。修改草稿后评测结果会自动清除。</small>
        </div>
      </form></section>
      <section class="card skill-preview-card"><h3>灰度命中预览</h3><div class="skill-release-form"><label>预览用户<input aria-label="预览用户" :value="previewSubject" maxlength="128" placeholder="viewer-7" @input="emit('update:preview-subject',$event.target.value)"></label><button class="run" :disabled="busyId==='preview'" @click="emit('preview')">预览命中</button><div v-if="previewResult" class="skill-preview-result"><span>稳定桶 {{previewResult.bucket}} / 99</span><b>命中 {{previewResult.selected_version||'无可用版本'}}</b><small>{{previewResult.selected_state}} · 灰度 {{previewResult.canary_percent}}%</small></div></div></section>
    </div>
    <section class="skill-version-section"><div class="model-section-head"><h2>版本历史</h2><label class="skill-canary-input">灰度比例<input aria-label="灰度比例" type="number" min="1" max="100" :value="canaryPercent" @input="emit('update:canary-percent',Number($event.target.value))"><span>%</span></label></div>
      <div v-if="loading" class="model-empty">正在加载…</div><div v-else-if="!versions.length" class="model-empty">暂无租户版本，当前使用内置版本。</div>
      <div v-else class="skill-version-list"><article v-for="version in versions" :key="version.id" class="skill-version-row"><div class="skill-version-main"><div><strong>{{version.definition.name}} · {{version.definition.version}}</strong><span class="skill-state" :class="version.state">{{stateLabel[version.state]||version.state}}<template v-if="version.state==='canary'"> · {{version.canary_percent}}%</template></span></div><p>{{version.definition.description}}</p><small>{{version.definition.id}} · {{fmt(version.updated_at)}} · {{version.created_by||'system'}}</small></div><div class="provider-actions"><button v-if="version.state==='draft'||version.state==='canary'" class="ghost" :disabled="busyId===version.id" @click="emit('canary',version)">开始灰度</button><button v-if="version.state==='draft'||version.state==='canary'" class="run" :disabled="busyId===version.id" @click="emit('publish',version)">正式发布</button><button v-if="version.state==='retired'" class="ghost" :disabled="busyId===version.id" @click="emit('rollback',version)">回滚到此版本</button></div></article></div>
    </section>
  </main>
</template>

<style scoped>
.skill-draft-actions{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.skill-evaluation-result{display:grid;gap:6px;padding:12px;border:1px solid #d9e4e5;border-radius:6px;background:#f7fbfa;color:#40525d;font-size:11px}
.skill-evaluation-result strong{color:#172d33;font-size:12px}
.skill-evaluation-result small{color:#73828b;line-height:1.45}
.skill-evaluation-result ul{display:grid;gap:4px;margin:2px 0 0;padding-left:18px}
.skill-eval-notice{margin:0;padding:9px 11px;border-left:3px solid #6a9e91;background:#f3f8f6;color:#52636d;font-size:11px;line-height:1.5}
.skill-query-quality-result{display:grid;gap:9px;padding:12px;border:1px solid #d9e4e5;border-radius:6px;background:#f7fbfa;color:#40525d;font-size:11px;min-width:0}
.skill-query-quality-head{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}.skill-query-quality-head strong{color:#172d33;font-size:12px}.skill-query-quality-head span,.skill-query-quality-result>p,.skill-query-quality-result>small{color:#73828b;margin:0;line-height:1.45;overflow-wrap:anywhere}
.skill-quality-metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.skill-quality-metrics>div{display:grid;gap:5px;padding:9px;border:1px solid #e0e8e8;border-radius:5px;background:#fff}.skill-quality-metrics b{color:#172d33}.skill-quality-cases{display:grid;border:1px solid #e0e8e8;border-radius:5px;background:#fff}.skill-quality-case{display:grid;grid-template-columns:minmax(0,1.4fr) repeat(2,minmax(70px,1fr));gap:8px;padding:7px 9px;border-top:1px solid #edf1f3;overflow-wrap:anywhere}.skill-quality-case-head{border-top:0;color:#73828b}.skill-quality-case strong{display:grid;gap:2px;font-weight:600;color:#172d33}.skill-quality-case strong small{font-weight:400;color:#73828b;font-size:9px}
@media(max-width:480px){.skill-draft-actions>*{flex:1 1 100%}.skill-draft-actions button{width:100%}}
@media(max-width:560px){.skill-quality-metrics{grid-template-columns:1fr}.skill-quality-case{grid-template-columns:minmax(0,1fr) repeat(2,minmax(68px,.8fr));gap:5px;padding:7px;font-size:10px}}
</style>
