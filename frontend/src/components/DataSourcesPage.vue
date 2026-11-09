<script setup>
defineProps({
  datasources: { type: Array, default: () => [] },
  schema: { type: Object, default: () => ({}) },
  testResults: { type: Object, default: () => ({}) }
})
const emit = defineEmits(['back', 'test', 'refresh', 'browse'])
</script>

<template>
  <main class="admin">
    <header><div><span class="eyebrow">DATA SOURCES</span><h1>数据源管理</h1><p>查看已授权数据源，测试连接、浏览表字段并刷新 Schema。</p></div><button class="run" @click="emit('back')">← 返回查询</button></header>
    <section v-if="!datasources.length" class="card muted">当前账号暂无可用数据源。</section>
    <section class="card" v-for="ds in datasources" :key="ds.id">
      <div class="ds-head"><div><h3>{{ds.name}}</h3><p class="muted">{{ds.id}} · {{ds.backend}} · {{ds.readonly ? '只读' : '读写'}}</p></div><div class="head-actions"><button class="ghost" @click="emit('test', ds.id)">测试连接</button><button class="ghost" @click="emit('refresh', ds.id)">刷新 Schema</button><button class="ghost" @click="emit('browse', ds.id)">浏览表字段</button></div></div>
      <div v-if="testResults[ds.id]" class="ds-result" :class="{ok: testResults[ds.id].ok}">{{testResults[ds.id].ok ? '✓ ' : '✗ '}}{{testResults[ds.id].message}}</div>
      <pre v-if="schema[ds.id]" class="ds-schema">{{JSON.stringify(schema[ds.id], null, 2)}}</pre>
    </section>
  </main>
</template>
