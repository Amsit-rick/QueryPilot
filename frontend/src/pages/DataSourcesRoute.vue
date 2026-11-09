<script setup>
import { inject } from 'vue'
import { appContextKey } from '../appContext'
import DataSourcesPage from '../components/DataSourcesPage.vue'
const { datasources, dsSchema, dsTestResult, openView, testDs, refreshDs, browseDs } = inject(appContextKey)
</script>
<template><DataSourcesPage :datasources="datasources" :schema="dsSchema" :test-results="dsTestResult" @back="openView('query')" @test="testDs" @refresh="refreshDs" @browse="browseDs" /></template>
