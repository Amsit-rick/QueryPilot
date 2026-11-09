<script setup>
import { inject } from 'vue'
import { appContextKey } from '../appContext'
import TaskListPage from '../components/TaskListPage.vue'
const { tasks, taskError, openView, newQuery } = inject(appContextKey)
</script>
<template><TaskListPage :tasks="tasks" :error="taskError" @open-task="id => openView('task', {id})" @new-query="newQuery" /></template>
