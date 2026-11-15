<script setup>
import { computed, ref } from 'vue'
import ChartConfigurator from './ChartConfigurator.vue'

const props = defineProps({
  result: { type: Object, required: true },
  canExport: Boolean,
  exportJob: { type: Object, default: null }
})
const emit = defineEmits(['export', 'download', 'copy'])
const sortKey = ref(null)
const sortDir = ref(1)
const columns = computed(() => props.result?.columns || [])
const rows = computed(() => props.result?.rows || [])
const usage = computed(() => props.result?.usage || null)
const usageSourceLabel = computed(() => ({
  provider: '供应商实测',
  estimate: '系统估算',
  none: '未调用模型'
})[usage.value?.usage_source] || '计量口径未知')
const formattedCost = computed(() => Number(usage.value?.cost_cny || 0).toLocaleString('zh-CN', {
  minimumFractionDigits: 6,
  maximumFractionDigits: 8,
  useGrouping: false
}))
const sortedRows = computed(() => {
  const list = rows.value.slice()
  if (sortKey.value === null) return list
  const key = sortKey.value
  return list.sort((left, right) => {
    const a = left[key]
    const b = right[key]
    const comparison = typeof a === 'number' && typeof b === 'number'
      ? a - b
      : String(a ?? '').localeCompare(String(b ?? ''), 'zh')
    return comparison * sortDir.value
  })
})
function toggleSort(index) {
  if (sortKey.value === index) sortDir.value *= -1
  else { sortKey.value = index; sortDir.value = 1 }
}
</script>

<template>
  <section class="result">
    <div class="result-head"><div><span class="eyebrow">RESULT</span><h2>{{result.answer}}</h2><div v-if="usage" class="usage-summary" aria-label="本轮模型用量"><span>输入 {{usage.input_tokens}}</span><span>输出 {{usage.output_tokens}}</span><span>共 {{usage.total_tokens}} Token</span><strong class="usage-cost">{{usage.cost_cny_estimated ? '约 ' : ''}}¥{{formattedCost}}</strong><small class="usage-source">{{usageSourceLabel}}</small></div><div v-else class="usage-summary usage-unavailable">历史任务未记录 Token 用量</div></div><div class="result-actions"><span class="count">{{result.row_count}} 行<span v-if="result.truncated"> · 已截断</span></span><button v-if="canExport" class="ghost" :disabled="exportJob && ['queued', 'running'].includes(exportJob.status)" @click="emit('export')">导出 CSV</button></div></div>
    <div v-if="exportJob" class="export-status"><template v-if="exportJob.status==='completed'"><a href="#" @click.prevent="emit('download', exportJob)">下载 CSV（{{exportJob.row_count}} 行）</a></template><template v-else-if="exportJob.status==='failed'">导出失败<span v-if="exportJob.error">：{{exportJob.error}}</span></template><template v-else>导出中…</template></div>
    <ChartConfigurator v-if="rows.length" :table="result" />
    <div v-if="!rows.length" class="empty-result">查询成功，但没有匹配的数据。可以尝试放宽筛选条件。</div>
    <div v-else class="table-wrap"><table><thead><tr><th v-for="(column,index) in columns" :key="column" class="sortable" @click="toggleSort(index)">{{column}}<span v-if="sortKey===index" class="sort-arrow">{{sortDir>0?' ▲':' ▼'}}</span></th></tr></thead><tbody><tr v-for="(row,index) in sortedRows" :key="index"><td v-for="(value,cell) in row" :key="cell" title="点击复制" @click="emit('copy', value)"><span class="cell-value">{{value === null || value === undefined ? '—' : value}}</span></td></tr></tbody></table></div>
  </section>
</template>
