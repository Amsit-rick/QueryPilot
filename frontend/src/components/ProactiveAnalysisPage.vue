<script setup>
import {computed} from 'vue'

const props=defineProps({status:{type:Object,default:null},plans:{type:Array,default:()=>[]},runs:{type:Object,default:()=>({})},draft:{type:Object,required:true},datasources:{type:Array,default:()=>[]},error:{type:String,default:''},loading:Boolean,busyId:{type:String,default:''}})
const emit=defineEmits(['refresh','back','create','transition','load-runs','open-task'])
const stateLabel={draft:'草稿',active:'运行中',paused:'已暂停',closed:'已关闭'}
const runLabel={completed:'已完成',approval_required:'等待审批',failed:'失败',skipped_budget:'预算限制'}
const activeCount=computed(()=>props.plans.filter(item=>item.state==='active').length)
function fmt(value){return value?String(value).slice(0,19).replace('T',' '):'—'}
</script>

<template>
  <main class="admin proactive-page">
    <header><div><span class="eyebrow">PROACTIVE ANALYSIS</span><h1>主动分析计划</h1><p>仅执行你显式启用的计划。每次运行都会重新校验账号、数据源、预算、安全策略和审批。</p></div><div class="head-actions"><button class="ghost" :disabled="loading" @click="emit('refresh')">刷新</button><button class="run" @click="emit('back')">← 返回查询</button></div></header>
    <div v-if="error" class="error">{{error}}</div>
    <section class="proactive-summary">
      <div><span>运行开关</span><b :class="status?.enabled?'state-ok':'state-warn'">{{status?.enabled?'已显式启用':'未启用'}}</b></div>
      <div><span>我的计划</span><b>{{plans.length}}</b></div><div><span>运行中</span><b>{{activeCount}}</b></div><div><span>最小间隔</span><b>{{status?.min_interval_minutes||'—'}} 分钟</b></div>
    </section>
    <div v-if="status&&!status.enabled" class="proactive-warning">当前后端未启用主动执行。可以保存草稿，但启用和 Runner 执行会失败关闭。</div>
    <section class="card proactive-create-card"><div class="model-section-head"><div><h2>创建计划</h2><p>计划创建后保持草稿，只有点击“启用”才会进入到期队列。</p></div></div>
      <form class="proactive-form" @submit.prevent="emit('create')">
        <label class="wide">分析问题<textarea v-model="draft.question" aria-label="分析问题" maxlength="2000" rows="3" placeholder="例如：每小时检查区域销售额是否出现异常下降" required></textarea></label>
        <label>数据源<select v-model="draft.datasource_id" aria-label="数据源" required><option v-for="item in datasources" :key="item.id" :value="item.id">{{item.name}}</option></select></label>
        <label>执行间隔（分钟）<input v-model.number="draft.interval_minutes" aria-label="执行间隔（分钟）" type="number" :min="status?.min_interval_minutes||15" max="10080" required></label>
        <label>每日最多执行<input v-model.number="draft.max_runs_per_day" aria-label="每日最多执行" type="number" min="1" :max="status?.max_runs_per_day||24" required></label>
        <label>每日预算（美元）<input v-model.number="draft.daily_budget_usd" aria-label="每日预算（美元）" type="number" min="0" :max="status?.max_daily_budget_usd||2" step="0.01" required></label>
        <label class="check wide"><input v-model="draft.require_approval" type="checkbox"><span>每次运行默认进入人工审批</span></label>
        <div class="wide proactive-boundary">Runner 是一次性扫描器，不在 API 进程内创建隐藏定时线程。部署系统需按批准频率调用 Runner。</div>
        <button class="run" type="submit" :disabled="busyId==='create'">{{busyId==='create'?'创建中…':'创建计划'}}</button>
      </form>
    </section>
    <section class="proactive-list-section"><div class="model-section-head"><h2>我的计划</h2><span>按账号与租户隔离</span></div>
      <div v-if="loading" class="model-empty">正在加载…</div><div v-else-if="!plans.length" class="model-empty">暂无主动分析计划。</div>
      <div v-else class="proactive-plan-list"><article v-for="plan in plans" :key="plan.id" class="proactive-plan-card">
        <div class="proactive-plan-head"><div><span class="proactive-state" :class="plan.state">{{stateLabel[plan.state]||plan.state}}</span><strong>{{plan.spec.question}}</strong></div><small>{{plan.spec.datasource_id}}</small></div>
        <div class="proactive-plan-meta"><span>每 {{plan.spec.interval_minutes}} 分钟</span><span>每日最多 {{plan.spec.max_runs_per_day}} 次</span><span>日预算 ${{plan.spec.daily_budget_usd}}</span><span>{{plan.spec.require_approval?'需要审批':'按安全门禁执行'}}</span></div>
        <div class="proactive-plan-time"><span>下次运行 {{fmt(plan.next_run_at)}}</span><span>今日 {{plan.runs_today}} 次 / ${{plan.cost_today_usd}}</span></div>
        <div class="provider-actions">
          <button v-if="plan.state==='draft'" class="run" :disabled="busyId===plan.id" @click="emit('transition',plan,'activate')">启用</button>
          <button v-if="plan.state==='active'" class="ghost" :disabled="busyId===plan.id" @click="emit('transition',plan,'pause')">暂停</button>
          <button v-if="plan.state==='paused'" class="run" :disabled="busyId===plan.id" @click="emit('transition',plan,'resume')">恢复</button>
          <button v-if="plan.state!=='closed'" class="ghost danger" :disabled="busyId===plan.id" @click="emit('transition',plan,'close')">关闭</button>
          <button class="ghost" :disabled="busyId===`runs:${plan.id}`" @click="emit('load-runs',plan)">运行记录</button>
          <button v-if="plan.last_task_id" class="ghost" @click="emit('open-task',plan.last_task_id)">打开最近任务</button>
        </div>
        <div v-if="runs[plan.id]" class="proactive-runs"><div v-if="!runs[plan.id].length" class="model-empty">尚无运行记录。</div><div v-for="run in runs[plan.id]" :key="run.id" class="proactive-run"><span>{{runLabel[run.status]||run.status}}</span><b>{{fmt(run.scheduled_for)}}</b><small>{{run.error_code||run.task_id||'—'}}</small></div></div>
      </article></div>
    </section>
  </main>
</template>

<style scoped>
.proactive-page{max-width:1120px}.proactive-page header p,.proactive-create-card p{margin:6px 0 0;color:#74838c;font-size:12px}.proactive-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;margin-bottom:18px;border:1px solid #dfe6e9;border-radius:7px;overflow:hidden;background:#dfe6e9}.proactive-summary>div{display:grid;gap:6px;padding:13px 15px;background:#fff}.proactive-summary span{font-size:10px;color:#7b8992}.proactive-summary b{font-size:13px}.proactive-warning,.proactive-boundary{padding:10px 12px;border-left:3px solid #d5a84d;background:#fff9ea;color:#715b2e;font-size:11px;line-height:1.5}.proactive-create-card{margin:0}.proactive-form{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px;margin-top:16px}.proactive-form label{display:grid;gap:6px;color:#687984;font-size:11px}.proactive-form .wide{grid-column:1/-1}.proactive-form textarea,.proactive-form input:not([type=checkbox]),.proactive-form select{width:100%;border:1px solid #dce4e8;border-radius:6px;padding:9px 10px;background:#fafcfb;color:#17202a;font:12px/1.5 Inter,Segoe UI,sans-serif}.proactive-form textarea{resize:vertical}.proactive-form .check{display:flex;align-items:center;gap:7px}.proactive-form .check input{accent-color:#27856f}.proactive-form .run{justify-self:start}.proactive-list-section{margin-top:24px}.proactive-plan-list{display:grid;gap:12px}.proactive-plan-card{display:grid;gap:12px;padding:16px;border:1px solid #dfe6e9;border-radius:8px;background:#fff}.proactive-plan-head,.proactive-plan-head>div,.proactive-plan-meta,.proactive-plan-time{display:flex;align-items:center;gap:10px}.proactive-plan-head{justify-content:space-between}.proactive-plan-head>div{min-width:0}.proactive-plan-head strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}.proactive-plan-head small,.proactive-plan-time{color:#7d8b93;font-size:10px}.proactive-state{flex:0 0 auto;padding:3px 7px;border-radius:9px;background:#edf2f3;color:#60717b;font-size:10px}.proactive-state.active{background:#e7f5ef;color:#24745f}.proactive-state.paused{background:#fff7df;color:#8a641f}.proactive-state.closed{background:#f1f1f1;color:#737f86}.proactive-plan-meta{flex-wrap:wrap}.proactive-plan-meta span{padding:4px 7px;border:1px solid #e0e6e8;border-radius:5px;color:#586871;font-size:10px}.proactive-plan-time{justify-content:space-between}.proactive-runs{display:grid;border-top:1px solid #edf1f3}.proactive-run{display:grid;grid-template-columns:110px 160px minmax(0,1fr);gap:10px;padding:9px 0;border-bottom:1px solid #edf1f3;font-size:10px}.proactive-run span{color:#326d5e}.proactive-run b{font-weight:500}.proactive-run small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#7d8b93}@media(max-width:760px){.proactive-summary,.proactive-form{grid-template-columns:1fr 1fr}.proactive-plan-head,.proactive-plan-time{align-items:flex-start;flex-direction:column}.proactive-run{grid-template-columns:1fr}.proactive-run small{white-space:normal;overflow-wrap:anywhere}}@media(max-width:440px){.proactive-summary,.proactive-form{grid-template-columns:1fr}.proactive-form .wide{grid-column:auto}.proactive-plan-head>div{align-items:flex-start;flex-direction:column}.provider-actions button{flex:1 1 110px}}
</style>
