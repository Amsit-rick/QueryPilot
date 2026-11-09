<script setup>
defineProps({ gateways:{type:Array,default:()=>[]}, states:{type:Object,default:()=>({})}, error:{type:String,default:''}, loading:{type:Boolean,default:false} })
const emit = defineEmits(['back','refresh','health','test','schema','select-table','preview'])
function statusLabel(status){ return {healthy:'正常',degraded:'降级',unavailable:'不可用'}[status] || '未检查' }
</script>

<template>
  <main class="admin gateway-page">
    <header><div><span class="eyebrow">DATA GATEWAYS</span><h1>数据网关</h1><p>{{loading?'正在同步网关状态':`${gateways.length} 个已授权网关`}}</p></div><div class="head-actions"><button class="ghost" :disabled="loading" @click="emit('refresh')">刷新</button><button class="run" @click="emit('back')">← 返回查询</button></div></header>
    <div v-if="error" class="error">{{error}}</div>
    <section v-if="!loading && !gateways.length" class="gateway-empty">当前账号暂无可用数据网关。</section>
    <section class="gateway-grid">
      <article v-for="gateway in gateways" :key="gateway.id" class="card gateway-card">
        <div class="gateway-head"><div><div class="gateway-title"><h3>{{gateway.name}}</h3><span class="status-dot" :class="states[gateway.id]?.health?.status"></span></div><p class="muted">{{gateway.id}} · {{gateway.backend}} / {{gateway.dialect}} · {{gateway.readonly?'只读':'读写'}}</p></div><strong class="gateway-health">{{statusLabel(states[gateway.id]?.health?.status)}}</strong></div>
        <p v-if="states[gateway.id]?.health?.message" class="gateway-message">{{states[gateway.id].health.message}}<span v-if="states[gateway.id].health.latency_ms"> · {{states[gateway.id].health.latency_ms}} ms</span></p>
        <div v-if="states[gateway.id]?.error" class="error">{{states[gateway.id].error}}</div>
        <div class="head-actions gateway-actions"><button class="ghost" :disabled="states[gateway.id]?.busy" @click="emit('test',gateway.id)">测试连接</button><button class="ghost" :disabled="states[gateway.id]?.busy" @click="emit('health',gateway.id)">健康状态</button><button class="ghost" :disabled="states[gateway.id]?.busy" @click="emit('schema',gateway.id)">读取 Schema</button></div>
        <div v-if="states[gateway.id]?.schema" class="gateway-schema"><label>数据表<select :value="states[gateway.id]?.selectedTable" @change="emit('select-table',gateway.id,$event.target.value)"><option v-for="(_,table) in states[gateway.id].schema" :key="table" :value="table">{{table}}</option></select></label><div class="gateway-columns"><span v-for="column in states[gateway.id].schema[states[gateway.id]?.selectedTable] || []" :key="column">{{column}}</span></div><button class="run" :disabled="states[gateway.id]?.busy || !states[gateway.id]?.selectedTable" @click="emit('preview',gateway.id,0)">预览数据</button></div>
        <div v-if="states[gateway.id]?.preview" class="gateway-preview"><div class="gateway-preview-meta"><strong>{{states[gateway.id].selectedTable}}</strong><span>{{states[gateway.id].preview.row_count}} 行{{states[gateway.id].preview.truncated?' · 尚有更多':''}}</span></div><div class="table-scroll"><table><thead><tr><th v-for="column in states[gateway.id].preview.columns" :key="column">{{column}}</th></tr></thead><tbody><tr v-for="(row,index) in states[gateway.id].preview.rows" :key="index"><td v-for="(value,columnIndex) in row" :key="columnIndex">{{value ?? 'NULL'}}</td></tr></tbody></table></div><div class="gateway-pager"><button class="ghost" :disabled="!states[gateway.id].previewOffset || states[gateway.id]?.busy" @click="emit('preview',gateway.id,Math.max(0,(states[gateway.id].previewOffset||0)-20))">上一页</button><button class="ghost" :disabled="!states[gateway.id].preview.truncated || states[gateway.id]?.busy" @click="emit('preview',gateway.id,(states[gateway.id].previewOffset||0)+20)">下一页</button></div></div>
      </article>
    </section>
  </main>
</template>
