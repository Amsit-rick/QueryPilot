<script setup>
import {inject} from 'vue'
import {appContextKey} from '../appContext'
import ProactiveAnalysisPage from '../components/ProactiveAnalysisPage.vue'
const {proactiveStatus,proactivePlans,proactiveRuns,proactiveDraft,proactiveError,proactiveLoading,proactiveBusyId,datasources,loadProactiveAnalysis,createProactivePlan,transitionProactivePlan,loadProactiveRuns,resumeTaskConversation,openView}=inject(appContextKey)
</script>
<template><ProactiveAnalysisPage :status="proactiveStatus" :plans="proactivePlans" :runs="proactiveRuns" :draft="proactiveDraft" :datasources="datasources" :error="proactiveError" :loading="proactiveLoading" :busy-id="proactiveBusyId" @refresh="loadProactiveAnalysis" @back="openView('query')" @create="createProactivePlan" @transition="transitionProactivePlan" @load-runs="loadProactiveRuns" @open-task="resumeTaskConversation" /></template>
