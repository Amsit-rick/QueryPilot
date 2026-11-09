<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import QueryComposer from './QueryComposer.vue'
import QueryResultPanel from './QueryResultPanel.vue'

const props = defineProps({
  result: { type: Object, default: null },
  error: { type: String, default: '' },
  approvalNotice: { type: Object, default: null },
  datasources: { type: Array, default: () => [] },
  loading: Boolean,
  activeDatasourceName: { type: String, default: '—' },
  apiOnline: Boolean,
  canExport: Boolean,
  exportJob: { type: Object, default: null },
  sql: { type: String, default: '' },
  api: { type: String, default: '' },
  conversationTaskId: { type: String, default: '' },
  conversationStatus: { type: String, default: '' },
  conversationTitle: { type: String, default: '' },
  conversationTurns: { type: Array, default: () => [] },
  conversationLoading: Boolean,
  conversationError: { type: String, default: '' },
  branchDraft: { type: Object, default: null },
  branchOrigin: { type: Object, default: null },
  branchFamily: { type: Array, default: () => [] },
  branchFamilyLoading: Boolean,
})
const question = defineModel('question', { type: String, required: true })
const activeDatasource = defineModel('activeDatasource', { type: String, required: true })
const requireApproval = defineModel('requireApproval', { type: Boolean, required: true })
const inspectorOpen = defineModel('inspectorOpen', { type: Boolean, required: true })
const emit = defineEmits(['question-edit', 'submit', 'cancel', 'open-approvals', 'use-prompt', 'export', 'download', 'copy', 'branch', 'cancel-branch', 'open-task'])
const showSql = ref(true)
const thread = ref(null)
const conversationActive = computed(() => Boolean(props.conversationTaskId || props.conversationTurns.length))
const canContinue = computed(() => Boolean(props.branchDraft) || !props.conversationTaskId || ['completed', 'failed'].includes(props.conversationStatus))
const latestResultTurnId = computed(() => [...props.conversationTurns].reverse().find(turn => turn.result)?.id || '')
watch(() => [props.conversationTurns.length, props.loading], async () => {
  await nextTick()
  if (thread.value) thread.value.scrollTop = thread.value.scrollHeight
})
</script>

<template>
  <main class="query-workbench" :class="{'has-conversation': conversationActive}">
    <div class="workspace-bar"><div><span class="workspace-kicker">{{conversationActive ? '多轮对话' : '新建任务'}}</span><h1>{{conversationTitle || '你的数据，直接问。'}}</h1></div><div class="workspace-bar-actions"><span class="workspace-chip">{{activeDatasourceName}}</span><span class="status"><i :class="{offline: !apiOnline}"></i> {{apiOnline ? '就绪' : 'API 离线'}}</span><button class="console-toggle" @click="inspectorOpen=!inspectorOpen">{{inspectorOpen?'关闭控制台':'控制台'}}</button></div></div>
    <section v-if="!conversationActive && !error && !approvalNotice" class="starter-panel" aria-label="常用分析模板">
      <div class="starter-icon">▦</div><h2>数据分析工作台</h2><p>用自然语言提问。Agent 会检查 Schema、执行只读 SQL，并在任务中保留完整追踪。</p><div class="starter-tags"><span>✧ SQL 查询</span><span>▱ Schema 检查</span><span>⌁ 数据追踪</span></div>
      <button class="starter-card" @click="emit('use-prompt', '展示当前数据源的所有表和主要字段')"><strong>检查 Schema</strong><small>列出表和字段，快速了解当前数据源</small><em>使用此提示</em></button><button class="starter-card" @click="emit('use-prompt', '查询近 30 天的销售额，并按日期汇总')"><strong>运行 SQL 查询</strong><small>从自然语言生成并执行只读 SQL</small><em>使用此提示</em></button><button class="starter-card" @click="emit('use-prompt', '分析每月销售趋势并找出波动最大的月份')"><strong>分析趋势</strong><small>探索指标变化与异常点</small><em>使用此提示</em></button>
    </section>
    <section v-if="conversationActive || conversationLoading || conversationError" ref="thread" class="conversation-thread" aria-live="polite">
      <div v-if="branchDraft" class="branch-origin-banner branch-draft-banner"><div><strong>新建分析分支</strong><span>来源轮次：{{branchDraft.source_question}}</span></div><button class="ghost" type="button" @click="emit('cancel-branch')">取消分支</button></div>
      <div v-else-if="branchOrigin" class="branch-origin-banner"><div><strong>分析分支 · {{branchOrigin.label || '未命名分支'}}</strong><span>该任务独立执行，并重新应用当前账号的权限、安全校验与审批策略。</span></div><button class="ghost" type="button" @click="emit('open-task', branchOrigin.parent_task_id)">返回来源任务</button></div>
      <div v-if="conversationLoading" class="conversation-state">正在恢复对话…</div>
      <div v-else-if="conversationError" class="error">{{conversationError}}</div>
      <article v-for="turn in conversationTurns" :key="turn.id" class="conversation-turn">
        <div class="message-row user-row"><div class="message-avatar">你</div><div class="user-message">{{turn.question}}</div></div>
        <div class="message-row assistant-row"><div class="message-avatar assistant-avatar">Q</div><div class="assistant-message">
          <div v-if="turn.status === 'running'" class="turn-progress"><span></span><span></span><span></span></div>
          <div v-else-if="turn.status === 'failed'" class="turn-error">{{turn.error || '查询失败'}}</div>
          <div v-else-if="turn.status === 'approval_required'" class="approval-notice"><strong>查询已提交审批</strong><span>{{turn.message}}</span><button class="ghost" @click="emit('open-approvals')">打开审批中心</button></div>
          <template v-else-if="turn.result"><QueryResultPanel :result="turn.result" :can-export="canExport && turn.id === latestResultTurnId" :export-job="turn.id === latestResultTurnId ? exportJob : null" @export="emit('export')" @download="job => emit('download', job)" @copy="value => emit('copy', value)" /><button class="branch-action" type="button" @click="emit('branch', turn)">从此轮创建分析分支</button></template>
          <div v-else class="conversation-state">本轮已完成，暂无可展示的结果资产。</div>
        </div></div>
      </article>
      <section v-if="branchFamilyLoading || branchFamily.length > 1" class="branch-family" aria-label="分析分支比较"><div class="branch-family-head"><div><span class="eyebrow">BRANCH ANALYSIS</span><h2>假设对比</h2></div><small>仅展示脱敏摘要，不包含 SQL 和结果明细</small></div><div v-if="branchFamilyLoading" class="conversation-state">正在加载分析分支…</div><div v-else class="branch-family-list"><button v-for="item in branchFamily" :key="item.task_id" type="button" class="branch-family-item" :class="{active: item.task_id === conversationTaskId}" @click="emit('open-task', item.task_id)"><span><b>{{item.kind === 'main' ? '主任务' : `分支 L${item.depth}`}}</b><strong>{{item.label || item.title}}</strong></span><span><em>{{item.status}}</em><small>{{item.row_count}} 行 · {{(item.columns || []).length}} 字段</small></span><p>{{item.answer || '暂无完成结果'}}</p></button></div></section>
    </section>
    <QueryComposer v-model:question="question" v-model:active-datasource="activeDatasource" v-model:require-approval="requireApproval" :datasources="datasources" :loading="loading" :conversation-active="conversationActive" :branch-mode="Boolean(branchDraft)" :can-continue="canContinue" @question-edit="emit('question-edit')" @submit="emit('submit')" @cancel="emit('cancel')" />
    <div v-if="error && !conversationTurns.length" class="error">{{error}}</div>
  </main>
  <aside v-if="inspectorOpen" class="inspector"><div class="inspector-head"><span>执行控制台</span><button @click="showSql=!showSql">{{showSql?'隐藏 SQL':'显示 SQL'}}</button></div><div v-if="showSql" class="code"><div class="code-head"><span>SQL</span><button @click="emit('copy', result?.sql || sql)">复制</button></div><pre>{{result?.sql || sql || '等待生成 SQL'}}</pre></div><div class="meta"><div><span>安全模式</span><b>只读 SELECT</b></div><div><span>数据源</span><b>{{activeDatasourceName}}</b></div><div><span>追踪 ID</span><b>{{result?.trace_id || '等待执行'}}</b></div></div><a class="docs" :href="`${api}/docs`" target="_blank">打开 API 文档 ↗</a></aside>
</template>
