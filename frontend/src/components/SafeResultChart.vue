<script setup>
import { computed } from 'vue'
import { parseChartSpec } from '../utils/chartSpec'
import '../chart-axes.css'

const props = defineProps({
  spec: { type: Object, required: true },
  table: { type: Object, required: true }
})

const palette = ['#217a68', '#d97706', '#39708e', '#a14f63', '#7661a8', '#3c7f8f', '#a6662e', '#627142']
const plot = {left: 112, right: 900, top: 36, bottom: 330}
const compactNumber = new Intl.NumberFormat('zh-CN', {notation: 'compact', maximumSignificantDigits: 5})
const columns = computed(() => Array.isArray(props.table?.columns) ? props.table.columns : [])
const parsedSpec = computed(() => parseChartSpec(props.spec, columns.value))
const limitedRows = computed(() => (Array.isArray(props.table?.rows) ? props.table.rows : []).slice(0, parsedSpec.value?.max_points || 0))
const xIndex = computed(() => columns.value.indexOf(parsedSpec.value?.x_column))
const yIndexes = computed(() => (parsedSpec.value?.y_columns || []).map(column => columns.value.indexOf(column)))
const series = computed(() => yIndexes.value.map((index, seriesIndex) => ({
  name: parsedSpec.value.y_columns[seriesIndex],
  color: palette[seriesIndex % palette.length],
  values: limitedRows.value.map((row, rowIndex) => ({
    label: String(row[xIndex.value] ?? '—'),
    x: row[xIndex.value],
    value: Number(row[index]),
    rowIndex
  })).filter(point => Number.isFinite(point.value))
})))
const allValues = computed(() => series.value.flatMap(item => item.values.map(point => point.value)))
const barPoints = computed(() => series.value.flatMap(item => item.values.map(point => ({...point, seriesName: item.name, color: item.color}))))
const maxAbs = computed(() => Math.max(...allValues.value.map(Math.abs), 1))
const barTicks = computed(() => Array.from({length: 5}, (_, index) => maxAbs.value * index / 4))
const valueRange = computed(() => {
  let min = Math.min(...allValues.value, 0)
  let max = Math.max(...allValues.value, 0)
  if (parsedSpec.value?.kind !== 'column') {
    min = Math.min(...allValues.value, 0)
    max = Math.max(...allValues.value, 0)
    if (min === 0 && allValues.value.length) min = Math.min(...allValues.value)
    if (max === 0 && allValues.value.length) max = Math.max(...allValues.value)
    const span = max - min || Math.max(Math.abs(max) * 0.1, 1)
    min -= span * 0.08
    max += span * 0.08
  }
  if (parsedSpec.value?.kind === 'column') {
    min = Math.min(0, ...allValues.value)
    max = Math.max(0, ...allValues.value)
  }
  const span = max - min || 1
  return {min, max, span}
})
function niceStep(span, intervals = 5) {
  const raw = Math.abs(span) / intervals
  if (!Number.isFinite(raw) || raw === 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const fraction = raw / magnitude
  const factor = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10
  return factor * magnitude
}
function ticksFor(min, max, intervals = 5) {
  const step = niceStep(max - min, intervals)
  const first = Math.ceil(min / step) * step
  const ticks = []
  for (let value = first, count = 0; value <= max + step * 1e-8 && count < 8; value += step, count += 1) {
    ticks.push(Math.abs(value) < step * 1e-10 ? 0 : value)
  }
  return ticks.length ? ticks : [min, max]
}
const yTickValues = computed(() => ticksFor(valueRange.value.min, valueRange.value.max))
const yPosition = value => plot.bottom - ((value - valueRange.value.min) / valueRange.value.span) * (plot.bottom - plot.top)
const xPosition = index => plot.left + (index + 0.5) * (plot.right - plot.left) / Math.max(limitedRows.value.length, 1)
const xTickIndexes = computed(() => {
  const count = limitedRows.value.length
  if (!count) return []
  const steps = Math.min(count, 6)
  return Array.from({length: steps}, (_, index) => steps === 1 ? 0 : Math.round(index * (count - 1) / (steps - 1)))
})
const xTickLabels = computed(() => xTickIndexes.value.map(index => ({
  index,
  label: String(limitedRows.value[index]?.[xIndex.value] ?? '—').slice(0, 14)
})))
const lineSeries = computed(() => series.value.map(item => ({
  ...item,
  points: item.values.map(point => ({...point, cx: xPosition(point.rowIndex), cy: yPosition(point.value)}))
})))
const columnBars = computed(() => {
  const firstSeries = series.value[0]
  if (!firstSeries) return []
  const width = Math.max(2.5, Math.min(90, (plot.right - plot.left) / Math.max(limitedRows.value.length, 1) * 0.62))
  return firstSeries.values.map(point => {
    const valueY = yPosition(point.value)
    const zeroY = yPosition(0)
    return {...point, x: xPosition(point.rowIndex) - width / 2, y: Math.min(valueY, zeroY), width, height: Math.max(Math.abs(zeroY - valueY), 0.8), color: firstSeries.color}
  })
})
const scatterValues = computed(() => (series.value[0]?.values || [])
  .map(point => Number(point.x))
  .filter(Number.isFinite))
const scatterXRange = computed(() => {
  let min = Math.min(...scatterValues.value)
  let max = Math.max(...scatterValues.value)
  const span = max - min || Math.max(Math.abs(max) * 0.1, 1)
  min -= span * 0.08
  max += span * 0.08
  return {min, max, span: max - min || 1}
})
const scatterXPosition = value => plot.left + ((value - scatterXRange.value.min) / scatterXRange.value.span) * (plot.right - plot.left)
const normalizedScatterPoints = computed(() => (series.value[0]?.values || [])
  .map(point => ({...point, numericX: Number(point.x)}))
  .filter(point => Number.isFinite(point.numericX))
  .map(point => ({...point, cx: scatterXPosition(point.numericX), cy: yPosition(point.value)})))
const scatterXTickValues = computed(() => ticksFor(scatterXRange.value.min, scatterXRange.value.max))
const pieSlices = computed(() => {
  const values = (series.value[0]?.values || []).filter(point => point.value > 0)
  const total = values.reduce((sum, point) => sum + point.value, 0)
  let offset = 0
  return values.map((point, index) => {
    const start = offset
    const size = total ? point.value / total * 100 : 0
    offset += size
    return {...point, color: palette[index % palette.length], start, end: offset, percent: size}
  })
})
const pieBackground = computed(() => `conic-gradient(${pieSlices.value.map(slice => `${slice.color} ${slice.start}% ${slice.end}%`).join(',')})`)
const hasData = computed(() => {
  if (!parsedSpec.value) return false
  if (parsedSpec.value.kind === 'scatter') return normalizedScatterPoints.value.length > 0
  if (parsedSpec.value.kind === 'pie') return pieSlices.value.length > 0
  return allValues.value.length > 0
})
const kindLabel = computed(() => ({bar:'条形图', column:'竖向柱状图', line:'折线图', pie:'饼图', scatter:'散点图'})[parsedSpec.value?.kind] || '')
function formatNumber(value) { return Number(value).toLocaleString('zh-CN', {maximumFractionDigits: 2}) }
function formatAxisNumber(value) { return compactNumber.format(Number(value)) }
</script>

<template>
  <div v-if="!parsedSpec" class="chart-unavailable" role="status">图表配置未通过安全校验，请使用表格查看结果。</div>
  <div v-else-if="!hasData" class="chart-unavailable" role="status">当前预览数据不足以绘制图表，请使用表格查看结果。</div>
  <figure v-else class="safe-chart" data-testid="safe-chart">
    <figcaption><strong>{{kindLabel}}</strong><span>{{parsedSpec.reason}}</span></figcaption>
    <div v-if="parsedSpec.kind === 'bar'" class="asset-bars">
      <div class="asset-bar-axis"><span></span><div><i v-for="tick in barTicks" :key="tick">{{formatAxisNumber(tick)}}</i></div><span></span></div>
      <div v-for="item in barPoints" :key="`${item.seriesName}-${item.rowIndex}-${item.label}`" class="asset-bar-row">
        <span :title="item.label">{{item.label}}<small v-if="series.length > 1"> · {{item.seriesName}}</small></span><div><i :style="{width: `${Math.max(Math.abs(item.value) / maxAbs * 100, 2)}%`, background: item.color}"></i></div><b>{{formatNumber(item.value)}}</b>
      </div>
    </div>
    <div v-else-if="parsedSpec.kind === 'pie'" class="asset-pie-layout">
      <div class="asset-pie" :style="{background: pieBackground}" role="img" :aria-label="`${parsedSpec.x_column}占比`"></div>
      <div class="asset-legend"><div v-for="slice in pieSlices" :key="`${slice.rowIndex}-${slice.label}`"><i :style="{background: slice.color}"></i><span>{{slice.label}}</span><b>{{slice.percent.toFixed(1)}}%</b></div></div>
    </div>
    <div v-else class="asset-plot">
      <svg viewBox="0 0 960 430" preserveAspectRatio="xMidYMid meet" :aria-label="kindLabel" role="img">
        <line :x1="plot.left" :y1="plot.bottom" :x2="plot.right" :y2="plot.bottom" class="plot-axis"/><line :x1="plot.left" :y1="plot.top" :x2="plot.left" :y2="plot.bottom" class="plot-axis"/>
        <g v-for="tick in yTickValues" :key="`y-${tick}`"><line :x1="plot.left" :y1="yPosition(tick)" :x2="plot.right" :y2="yPosition(tick)" class="plot-grid"/><line :x1="plot.left - 6" :y1="yPosition(tick)" :x2="plot.left" :y2="yPosition(tick)" class="plot-axis"/><text :x="plot.left - 12" :y="yPosition(tick) + 5" text-anchor="end" class="plot-tick-label">{{formatAxisNumber(tick)}}</text></g>
        <template v-if="parsedSpec.kind !== 'scatter'"><g v-for="item in xTickLabels" :key="`x-${item.index}`"><line :x1="xPosition(item.index)" :y1="plot.bottom" :x2="xPosition(item.index)" :y2="plot.bottom + 6" class="plot-axis"/><text :x="xPosition(item.index)" y="358" text-anchor="middle" class="plot-tick-label">{{item.label}}</text></g></template>
        <template v-if="parsedSpec.kind === 'column'"><rect v-for="item in columnBars" :key="`${item.rowIndex}-${item.label}`" :x="item.x" :y="item.y" :width="item.width" :height="item.height" :fill="item.color" class="column-bar"><title>{{item.label}}: {{formatNumber(item.value)}}</title></rect></template>
        <template v-else-if="parsedSpec.kind === 'line'"><g v-for="item in lineSeries" :key="item.name"><polyline :points="item.points.map(point => `${point.cx},${point.cy}`).join(' ')" :stroke="item.color" class="plot-line"/><circle v-for="point in item.points" :key="point.rowIndex" :cx="point.cx" :cy="point.cy" r="4" :fill="item.color"><title>{{point.label}} · {{item.name}}: {{formatNumber(point.value)}}</title></circle></g></template>
        <template v-else><g v-for="tick in scatterXTickValues" :key="`sx-${tick}`"><line :x1="scatterXPosition(tick)" :y1="plot.bottom" :x2="scatterXPosition(tick)" :y2="plot.bottom + 6" class="plot-axis"/><text :x="scatterXPosition(tick)" y="358" text-anchor="middle" class="plot-tick-label">{{formatAxisNumber(tick)}}</text></g><circle v-for="point in normalizedScatterPoints" :key="point.rowIndex" :cx="point.cx" :cy="point.cy" r="5" :fill="palette[0]"><title>{{parsedSpec.x_column}}: {{point.x}} · {{parsedSpec.y_columns[0]}}: {{formatNumber(point.value)}}</title></circle></template>
        <text x="526" y="410" text-anchor="middle" class="plot-axis-label">{{parsedSpec.x_column}}</text><text x="28" y="185" text-anchor="middle" transform="rotate(-90 28 185)" class="plot-axis-label">{{parsedSpec.y_columns[0]}}</text>
      </svg>
      <div v-if="parsedSpec.kind === 'line' && lineSeries.length > 1" class="plot-legend"><span v-for="item in lineSeries" :key="item.name"><i :style="{background:item.color}"></i>{{item.name}}</span></div>
    </div>
  </figure>
</template>
