<script setup>
import { computed, ref, watch } from 'vue'
import SafeResultChart from './SafeResultChart.vue'
import { parseChartSpec } from '../utils/chartSpec'

const props = defineProps({
  table: { type: Object, required: true },
  initialSpec: { type: Object, default: null }
})

const kindLabels = { bar: '条形图', column: '竖向柱状图', line: '折线图', pie: '饼图', scatter: '散点图' }
const columns = computed(() => Array.isArray(props.table?.columns) ? props.table.columns.map(String) : [])
const rows = computed(() => Array.isArray(props.table?.rows) ? props.table.rows : [])

function isNumeric(value) {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') return false
  const number = Number(value)
  return Number.isFinite(number)
}

function isTemporal(value) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}(?:[T\s]|$)/.test(value)
}

const columnTypes = computed(() => columns.value.map((column, index) => {
  const values = rows.value.map(row => row?.[index]).filter(value => value !== null && value !== undefined && value !== '')
  if (values.length && values.every(isNumeric)) return 'numeric'
  if (values.length && values.every(isTemporal)) return 'temporal'
  return 'categorical'
}))
const numericColumns = computed(() => columns.value.filter((_, index) => columnTypes.value[index] === 'numeric'))
const dimensionColumns = computed(() => columns.value.filter((_, index) => ['categorical', 'temporal'].includes(columnTypes.value[index])))

const availableKinds = computed(() => {
  const kinds = []
  if (dimensionColumns.value.length && numericColumns.value.length) kinds.push('bar', 'column', 'line', 'pie')
  if (numericColumns.value.length >= 2) kinds.push('scatter')
  return kinds
})

const selectedKind = ref('bar')
const xColumn = ref('')
const yColumn = ref('')

function validInitialSpec(value) {
  if (!value || !availableKinds.value.includes(value.kind)) return null
  const parsed = parseChartSpec(value, columns.value)
  if (!parsed || !parsed.y_columns?.length) return null
  if (!columns.value.includes(parsed.x_column) || !columns.value.includes(parsed.y_columns[0])) return null
  return parsed
}

function chooseDefaults(preferred = null) {
  const kind = preferred?.kind && availableKinds.value.includes(preferred.kind)
    ? preferred.kind
    : availableKinds.value[0] || 'bar'
  selectedKind.value = kind
  const xOptions = kind === 'scatter' ? numericColumns.value : dimensionColumns.value
  xColumn.value = preferred?.x_column && xOptions.includes(preferred.x_column)
    ? preferred.x_column
    : xOptions[0] || ''
  const yOptions = numericColumns.value.filter(column => column !== xColumn.value)
  yColumn.value = preferred?.y_columns?.[0] && yOptions.includes(preferred.y_columns[0])
    ? preferred.y_columns[0]
    : yOptions[0] || ''
}

watch([availableKinds, () => props.initialSpec], () => chooseDefaults(validInitialSpec(props.initialSpec)), { immediate: true })
const xOptions = computed(() => selectedKind.value === 'scatter' ? numericColumns.value : dimensionColumns.value)
const yOptions = computed(() => numericColumns.value.filter(column => column !== xColumn.value))
watch(() => selectedKind.value, () => {
  if (!xOptions.value.includes(xColumn.value)) xColumn.value = xOptions.value[0] || ''
  if (!yOptions.value.includes(yColumn.value)) yColumn.value = yOptions.value[0] || ''
})
watch(() => xColumn.value, () => {
  if (!yOptions.value.includes(yColumn.value)) yColumn.value = yOptions.value[0] || ''
})

const draftSpec = computed(() => ({
  schema_version: '1.0',
  kind: selectedKind.value,
  x_column: xColumn.value,
  y_columns: yColumn.value ? [yColumn.value] : [],
  reason: '根据当前结果字段生成的图表',
  column_types: columnTypes.value,
  max_points: 200
}))
const safeSpec = computed(() => parseChartSpec(draftSpec.value, columns.value))
const unavailableReason = computed(() => {
  if (!rows.value.length) return '当前结果没有数据可供绘图。'
  if (!availableKinds.value.length) return '当前结果至少需要一个分类/时间字段和一个数值字段，或两个数值字段。'
  if (!safeSpec.value) return '请选择有效的图形、维度字段和数值字段。'
  return ''
})
</script>

<template>
  <section class="chart-configurator" data-testid="chart-configurator">
    <div class="chart-configurator-head">
      <div><strong>数据可视化</strong><small>基于当前查询结果选择图形和统计字段</small></div>
      <span v-if="safeSpec" class="chart-configurator-status">{{kindLabels[safeSpec.kind]}}</span>
    </div>
    <div class="chart-configurator-controls">
      <label>图形
        <select v-model="selectedKind" data-testid="chart-kind-select" :disabled="!availableKinds.length">
          <option v-for="kind in availableKinds" :key="kind" :value="kind">{{kindLabels[kind]}}</option>
        </select>
      </label>
      <label>X 轴 / 维度
        <select v-model="xColumn" data-testid="chart-x-select" :disabled="!xOptions.length">
          <option v-for="column in xOptions" :key="column" :value="column">{{column}}</option>
        </select>
      </label>
      <label>Y 轴 / 数值
        <select v-model="yColumn" data-testid="chart-y-select" :disabled="!yOptions.length">
          <option v-for="column in yOptions" :key="column" :value="column">{{column}}</option>
        </select>
      </label>
    </div>
    <div v-if="unavailableReason" class="chart-unavailable" role="status">{{unavailableReason}}</div>
    <SafeResultChart v-else :spec="safeSpec" :table="table" />
  </section>
</template>
