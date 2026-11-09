<script setup>
import { computed, ref } from 'vue'
import ChartConfigurator from './ChartConfigurator.vue'
import { parseChartSpec } from '../utils/chartSpec'

const props = defineProps({
  artifact: { type: Object, required: true },
  chartArtifact: { type: Object, default: null }
})
const metadata = computed(() => props.artifact?.metadata || {})
const spec = computed(() => props.chartArtifact?.metadata?.spec || null)
const validSpec = computed(() => parseChartSpec(spec.value, metadata.value.columns || []))
const chartBlocked = computed(() => Boolean(spec.value) && !validSpec.value)
const activeView = ref('chart')
</script>

<template>
  <div class="task-result">
    <div class="task-result-head">
      <div><strong>{{artifact.name}}</strong><small>返回 {{metadata.row_count || 0}} 行<template v-if="metadata.truncated"> · 结果已截断</template><template v-if="metadata.preview_count < metadata.row_count"> · 仅展示前 {{metadata.preview_count}} 行</template></small></div>
      <div class="result-view-switch" role="group" aria-label="结果展示方式">
        <button type="button" :aria-pressed="activeView === 'table'" @click="activeView = 'table'">表格</button>
        <button type="button" :aria-pressed="activeView === 'chart'" @click="activeView = 'chart'">图表</button>
      </div>
    </div>
    <ChartConfigurator v-if="activeView === 'chart' && !chartBlocked" :table="metadata" :initial-spec="validSpec" />
    <div v-else-if="activeView === 'chart'" class="chart-unavailable" role="status">图表配置未通过安全校验，请使用表格查看结果。</div>
    <template v-else>
      <div v-if="metadata.rows?.length" class="table-scroll"><table><thead><tr><th v-for="column in metadata.columns" :key="column">{{column}}</th></tr></thead><tbody><tr v-for="(row, rowIndex) in metadata.rows" :key="rowIndex"><td v-for="(value, columnIndex) in row" :key="columnIndex">{{value ?? '—'}}</td></tr></tbody></table></div>
      <div v-else class="muted">查询成功，但没有匹配的数据。</div>
    </template>
    <div v-if="spec && !validSpec && activeView !== 'chart'" class="chart-contract-warning">图表资产未通过安全校验，已保留表格视图。</div>
  </div>
</template>
