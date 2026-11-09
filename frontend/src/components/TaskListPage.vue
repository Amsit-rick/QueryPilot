<script setup>
defineProps({
  tasks: { type: Array, default: () => [] },
  error: { type: String, default: '' },
})

const emit = defineEmits(['open-task', 'new-query'])

function taskStatus(status) {
  return ({ draft:'草稿', planning:'规划中', running:'执行中', verifying:'验证中', approval_required:'待审批', completed:'已完成', failed:'失败', cancelled:'已取消' })[status] || status
}

function fmtTime(value) {
  return value ? String(value).slice(0, 19).replace('T', ' ') : '—'
}
</script>

<template>
  <main class="admin task-page">
    <header>
      <div><span class="eyebrow">DATA TASKS</span><h1>数据任务</h1><p>查看当前账号创建的分析任务及执行状态。</p></div>
      <button class="run" @click="emit('new-query')">＋ 新建任务</button>
    </header>
    <div v-if="error" class="error">{{error}}</div>
    <section v-if="tasks.length" class="task-list">
      <button v-for="item in tasks" :key="item.id" class="task-row" @click="emit('open-task', item.id)">
        <span class="task-main"><strong>{{item.title || item.question}}</strong><small>{{item.datasource_id || '默认数据源'}} · {{fmtTime(item.updated_at || item.created_at)}}</small></span>
        <span class="task-state" :class="item.status">{{taskStatus(item.status)}}</span>
      </button>
    </section>
    <section v-else class="card task-empty"><h3>还没有数据任务</h3><p class="muted">从一个业务问题开始创建首个分析任务。</p><button class="run" @click="emit('new-query')">创建任务</button></section>
  </main>
</template>
