<script setup>
const props = defineProps({
  question: { type: String, default: '' },
  activeDatasource: { type: String, default: '' },
  datasources: { type: Array, default: () => [] },
  requireApproval: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  conversationActive: { type: Boolean, default: false },
  branchMode: { type: Boolean, default: false },
  canContinue: { type: Boolean, default: true },
})

const emit = defineEmits([
  'update:question',
  'update:activeDatasource',
  'update:requireApproval',
  'question-edit',
  'submit',
  'cancel',
])

function onQuestionInput(event) {
  emit('update:question', event.target.value)
  emit('question-edit')
}
</script>

<template>
  <section class="composer">
    <textarea :value="props.question" :placeholder="props.branchMode ? '输入分支假设…' : (props.conversationActive ? '继续提问…' : '询问一个数据问题，例如：查询各区域销售额')" :disabled="!props.canContinue && !props.loading" @keydown.ctrl.enter="emit('submit')" @input="onQuestionInput"></textarea>
    <div class="composer-foot">
      <div class="query-options">
        <button class="composer-add" type="button" title="添加上下文">＋</button>
        <select :value="props.activeDatasource" class="composer-datasource" aria-label="选择数据源" :title="props.conversationActive || props.branchMode ? '当前对话固定使用原数据源' : '选择本次查询使用的数据源'" :disabled="props.conversationActive || props.branchMode" @change="emit('update:activeDatasource', $event.target.value)">
          <option v-for="ds in props.datasources" :key="ds.id" :value="ds.id">{{ds.name}}</option>
          <option v-if="!props.datasources.length" value="" disabled>暂无可用数据源</option>
        </select>
        <label class="approval-toggle"><input :checked="props.requireApproval" type="checkbox" @change="emit('update:requireApproval', $event.target.checked)"><span>提交审批</span></label>
      </div>
      <button v-if="props.loading" class="ghost danger" @click="emit('cancel')">取消查询</button>
      <button v-else class="run composer-send" :disabled="!props.canContinue" @click="emit('submit')" :title="props.branchMode ? '创建分析分支' : (props.requireApproval?'提交审批':(props.conversationActive?'发送追问':'执行查询'))" :aria-label="props.branchMode ? '创建分析分支' : (props.requireApproval?'提交审批':(props.conversationActive?'发送追问':'执行查询'))">↑</button>
    </div>
  </section>
</template>
