<script setup>
import { inject, ref } from 'vue'
import { useRoute } from 'vue-router'
import { appContextKey } from '../appContext'
import TaskDetailPage from '../components/TaskDetailPage.vue'
const route = useRoute()
const { taskDetail, taskLoading, taskError, loadTask, followUpTask, submitTaskFeedback, openView } = inject(appContextKey)
const followUpBusy = ref(false)
const followUpError = ref('')
const feedbackBusy = ref(false)
const feedbackError = ref('')

async function handleFollowUp(question) {
  followUpBusy.value = true
  followUpError.value = ''
  try {
    await followUpTask(route.params.id, question)
  } catch (error) {
    followUpError.value = error?.message || '追问失败'
  } finally {
    followUpBusy.value = false
  }
}

async function handleFeedback(payload) {
  feedbackBusy.value = true
  feedbackError.value = ''
  try {
    await submitTaskFeedback(route.params.id, payload)
  } catch (error) {
    feedbackError.value = error?.message || '反馈提交失败'
  } finally {
    feedbackBusy.value = false
  }
}
</script>
<template><TaskDetailPage :task="taskDetail" :loading="taskLoading" :error="taskError" :follow-up-busy="followUpBusy" :follow-up-error="followUpError" :feedback-busy="feedbackBusy" :feedback-error="feedbackError" @refresh="loadTask(route.params.id)" @follow-up="handleFollowUp" @feedback="handleFeedback" @back="openView('tasks')" /></template>
