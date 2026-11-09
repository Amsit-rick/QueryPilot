<script setup>
import { computed, ref, toRef, watch } from 'vue'
import TaskResultArtifact from './TaskResultArtifact.vue'

const props = defineProps({
  task: { type: Object, default: null },
  loading: Boolean,
  error: { type: String, default: '' },
  followUpBusy: Boolean,
  followUpError: { type: String, default: '' },
  feedbackBusy: Boolean,
  feedbackError: { type: String, default: '' }
})
const task = toRef(props, 'task')
const loading = toRef(props, 'loading')
const error = toRef(props, 'error')
const followUpBusy = toRef(props, 'followUpBusy')
const followUpError = toRef(props, 'followUpError')
const feedbackBusy = toRef(props, 'feedbackBusy')
const feedbackError = toRef(props, 'feedbackError')
const followUpQuestion = ref('')
const feedbackOutcome = ref('accepted')
const feedbackNote = ref('')
const correctedSql = ref('')
const emit = defineEmits(['refresh', 'back', 'follow-up', 'feedback'])
const analysis = computed(() => [...(task.value?.steps || [])].reverse().find(step => step.kind === 'analyze_result' && step.status === 'completed')?.output || null)
const feedbackEvents = computed(() => (task.value?.events || []).filter(event => event.kind === 'feedback_recorded'))
const feedbackOptions = computed(() => task.value?.status === 'failed'
  ? [{value:'failed', label:'失败案例'}]
  : [{value:'accepted', label:'采纳'}, {value:'corrected', label:'修正'}, {value:'rejected', label:'拒绝'}])
watch(() => task.value?.status, status => { feedbackOutcome.value = status === 'failed' ? 'failed' : 'accepted' }, {immediate: true})

function taskStatus(status) {
  return ({draft:'草稿',planning:'规划中',running:'执行中',verifying:'验证中',approval_required:'待审批',clarification_required:'待澄清',completed:'已完成',failed:'失败',cancelled:'已取消'})[status] || status
}
function stepStatus(status) { return ({pending:'等待中',running:'执行中',completed:'已完成',failed:'失败'})[status] || status }
function stepLabel(step) { return step?.kind === 'reflection' ? '结果质量反思' : step?.kind }
function reflectionMode(value) { return ({off:'关闭',shadow:'影子观察',enforced:'强制执行'})[value] || value || '强制执行' }
function reflectionSource(value) { return ({deterministic:'确定性规则',semantic_critic:'语义 Critic',structural:'结构校验'})[value] || value || '确定性规则' }
function reflectionSummary(output) {
  if (output?.summary) return output.summary
  const problem = ({syntax:'SQL 语法问题',schema:'Schema 匹配问题',relationship:'表关系风险',aggregation_grain:'聚合粒度风险',business_ambiguity:'业务口径歧义',empty_result:'空结果风险',result_shape:'结果结构异常',execution_transient:'数据库瞬时错误',permission:'权限限制',security:'安全规则冲突',cost:'查询成本风险'})[output?.failure_type] || '查询质量问题'
  const action = ({regenerate_sql:'已重新生成查询',retrieve_more_schema:'已扩大授权范围内的 Schema 召回',retry_execution:'已按原查询重试',ask_clarification:'需要补充业务口径',require_approval:'需要人工审批',fail:'已停止执行',accept:'验证通过'})[output?.action] || '已记录处理动作'
  return `检测到${problem}，${action}。`
}
function eventLabel(event) { return event?.kind === 'reflection_recorded' ? '反思决策已记录' : event?.kind }
function taskSql(task) { return [...(task?.steps || [])].reverse().find(step => step.output?.sql)?.output?.sql || '' }
function tableArtifacts(task) { return (task?.artifacts || []).filter(item => item.kind === 'table' && Array.isArray(item.metadata?.columns)) }
function analysisMode(mode) { return ({descriptive_statistics:'描述统计',trend:'趋势分析',period_comparison:'同比环比',anomaly_detection:'异常检测',auto_insights:'综合洞察'})[mode] || mode }
function direction(value) { return ({up:'上升',down:'下降',flat:'持平'})[value] || value }
function comparisonKind(value) { return value === 'year_over_year' ? '同比' : '环比' }
function number(value) { return typeof value === 'number' ? new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 2}).format(value) : '—' }
function percent(value) { return typeof value === 'number' ? `${value > 0 ? '+' : ''}${number(value)}%` : '不可计算' }
function chartArtifactFor(task, tableArtifact) {
  const sourceKey = tableArtifact?.metadata?.idempotency_key
  if (!sourceKey) return null
  return (task?.artifacts || []).find(item => item.kind === 'chart' && item.metadata?.source_artifact_idempotency_key === sourceKey) || null
}
function fmtTime(value) { return value ? String(value).slice(0, 19).replace('T', ' ') : '—' }
function submitFollowUp() {
  const question = followUpQuestion.value.trim()
  if (!question || followUpBusy.value || task.value?.status !== 'completed') return
  emit('follow-up', question)
  followUpQuestion.value = ''
}
function submitFeedback() {
  if (feedbackBusy.value || !['completed', 'failed'].includes(task.value?.status)) return
  if (feedbackOutcome.value === 'corrected' && !correctedSql.value.trim()) return
  emit('feedback', {outcome: feedbackOutcome.value, note: feedbackNote.value.trim(), corrected_sql: feedbackOutcome.value === 'corrected' ? correctedSql.value.trim() : ''})
  feedbackNote.value = ''
  correctedSql.value = ''
}
</script>

<template>
  <main class="admin task-page">
    <header>
      <div><span class="eyebrow">TASK DETAIL</span><h1>{{task?.title || task?.question || '任务详情'}}</h1><p v-if="task">{{task.datasource_id}} · {{fmtTime(task.updated_at || task.created_at)}}</p></div>
      <div class="head-actions"><button class="ghost" @click="emit('refresh')">刷新</button><button class="run" @click="emit('back')">← 返回任务</button></div>
    </header>
    <div v-if="loading" class="card muted">正在加载任务详情…</div><div v-else-if="error" class="error">{{error}}</div>
    <template v-else-if="task">
      <section class="task-overview"><div><span>状态</span><b class="task-state" :class="task.status">{{taskStatus(task.status)}}</b></div><div><span>任务 ID</span><b>{{task.id}}</b></div><div><span>步骤</span><b>{{task.steps?.length || 0}}</b></div><div><span>结果资产</span><b>{{task.artifacts?.length || 0}}</b></div></section>
      <section class="card"><h3>分析问题</h3><p class="task-question">{{task.question}}</p><pre v-if="taskSql(task)" class="task-sql">{{taskSql(task)}}</pre></section>
      <section class="card"><h3>查询结果</h3><div v-if="!tableArtifacts(task).length" class="muted">暂无可展示的结果预览。</div><TaskResultArtifact v-for="artifact in tableArtifacts(task)" :key="artifact.id" :artifact="artifact" :chart-artifact="chartArtifactFor(task, artifact)" /></section>
      <section v-if="analysis" class="card analysis-panel">
        <div class="analysis-head"><div><span>{{analysisMode(analysis.mode)}}</span><h3>结果洞察</h3></div><small v-if="analysis.time_column">时间字段：{{analysis.time_column}}</small></div>
        <p class="analysis-summary">{{analysis.summary}}</p>
        <div v-if="analysis.trends?.length" class="analysis-list"><h4>趋势</h4><div v-for="item in analysis.trends" :key="`trend-${item.column}`" class="analysis-row"><div><strong>{{item.column}}</strong><small>{{item.start_period}} → {{item.end_period}} · {{item.points}} 个时间点</small></div><div class="analysis-value" :class="item.direction"><b>{{direction(item.direction)}}</b><span>{{percent(item.percent_change)}}</span></div></div></div>
        <div v-if="analysis.comparisons?.length" class="analysis-list"><h4>期间比较</h4><div v-for="item in analysis.comparisons" :key="`${item.kind}-${item.column}`" class="analysis-row"><div><strong>{{item.column}} · {{comparisonKind(item.kind)}}</strong><small>{{item.baseline_period}} → {{item.current_period}}</small></div><div class="analysis-value" :class="item.absolute_change > 0 ? 'up' : item.absolute_change < 0 ? 'down' : 'flat'"><b>{{percent(item.percent_change)}}</b><span>变化 {{number(item.absolute_change)}}</span></div></div></div>
        <div v-if="analysis.anomalies?.length" class="analysis-list"><h4>候选异常</h4><div v-for="item in analysis.anomalies" :key="`${item.column}-${item.period}`" class="analysis-row anomaly"><div><strong>{{item.column}} · {{item.period}}</strong><small>基准 {{number(item.baseline)}} · {{item.method}}</small></div><div class="analysis-value"><b>{{number(item.value)}}</b><span>偏离分 {{number(item.score)}}</span></div></div></div>
        <div v-if="analysis.limitations?.length" class="analysis-limitations"><strong>数据限制</strong><ul><li v-for="item in analysis.limitations" :key="item">{{item}}</li></ul></div>
      </section>
      <section v-if="['completed', 'failed'].includes(task.status)" class="card feedback-card">
        <div class="feedback-head"><h3>结果反馈</h3><small v-if="feedbackEvents.length">已记录 {{feedbackEvents.length}} 次</small></div>
        <form class="feedback-form" @submit.prevent="submitFeedback">
          <div class="feedback-options" role="group" aria-label="反馈类型"><button v-for="option in feedbackOptions" :key="option.value" type="button" :aria-pressed="feedbackOutcome === option.value" @click="feedbackOutcome = option.value">{{option.label}}</button></div>
          <textarea v-if="feedbackOutcome === 'corrected'" v-model="correctedSql" rows="4" maxlength="5000" spellcheck="false" placeholder="修正后的只读 SQL"></textarea>
          <input v-model="feedbackNote" maxlength="500" placeholder="补充说明（可选）" />
          <div class="feedback-actions"><span v-if="feedbackError" class="follow-up-error">{{feedbackError}}</span><button class="run" type="submit" :disabled="feedbackBusy || (feedbackOutcome === 'corrected' && !correctedSql.trim())">{{feedbackBusy ? '提交中…' : '记录反馈'}}</button></div>
        </form>
        <div v-if="feedbackEvents.length" class="feedback-history"><div v-for="event in feedbackEvents" :key="event.id"><span>{{({accepted:'已采纳', corrected:'已修正', rejected:'已拒绝', failed:'失败案例'})[event.payload?.outcome] || event.payload?.outcome}}</span><small>{{fmtTime(event.created_at)}}</small></div></div>
      </section>
      <section v-if="task.status === 'completed'" class="card follow-up-card"><h3>继续追问</h3><form class="follow-up-form" @submit.prevent="submitFollowUp"><textarea v-model="followUpQuestion" rows="3" maxlength="2000" placeholder="基于当前任务继续提问，例如：按区域拆分刚才的结果"></textarea><div class="follow-up-actions"><span v-if="followUpError" class="follow-up-error">{{followUpError}}</span><button class="run" type="submit" :disabled="followUpBusy || !followUpQuestion.trim()">{{followUpBusy ? '执行中…' : '提交追问'}}</button></div></form></section>
      <section class="card"><h3>执行步骤</h3><div v-if="!task.steps?.length" class="muted">任务尚未开始执行。</div><ol v-else class="timeline"><li v-for="step in task.steps" :key="step.id || step.sequence" :class="{'reflection-step': step.kind === 'reflection'}"><span class="timeline-marker" :class="step.status"></span><div><div class="timeline-head"><strong>{{stepLabel(step)}}</strong><span>{{stepStatus(step.status)}} · {{step.duration_ms || 0}} ms</span></div><template v-if="step.kind === 'reflection'"><p class="reflection-summary">{{reflectionSummary(step.output)}}</p><div class="reflection-meta"><span>第 {{step.output?.attempt || 1}} 次</span><span>{{reflectionMode(step.output?.mode)}}</span><span>{{reflectionSource(step.output?.source)}}</span></div><div v-if="step.output?.reason_codes?.length" class="reason-code-list"><code v-for="code in step.output.reason_codes" :key="code">{{code}}</code></div></template><p v-if="step.error" class="step-error">{{step.error}}</p><small>{{fmtTime(step.created_at)}}<template v-if="step.retry_count"> · 重试 {{step.retry_count}} 次</template></small></div></li></ol></section>
      <section class="card"><h3>任务事件</h3><div class="event-list"><div v-for="event in (task.events || [])" :key="event.id" class="event-row"><span>{{eventLabel(event)}}</span><small>{{fmtTime(event.created_at)}}</small></div><div v-if="!task.events?.length" class="muted">暂无事件记录。</div></div></section>
    </template>
  </main>
</template>
