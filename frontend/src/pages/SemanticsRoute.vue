<script setup>
import { inject } from 'vue'
import { appContextKey } from '../appContext'
import SemanticsPage from '../components/SemanticsPage.vue'
const { semantics,semanticDatasource,semanticDraft,semanticEditingId,semanticError,semanticLoading,semanticBusy,semanticHistory,semanticHistoryId,datasources,loadSemantics,selectSemanticDatasource,editSemantic,cancelSemanticEdit,saveSemantic,removeSemantic,loadSemanticHistory,openView } = inject(appContextKey)
</script>
<template><SemanticsPage v-model:datasource="semanticDatasource" :entries="semantics" :datasources="datasources" :draft="semanticDraft" :editing-id="semanticEditingId" :history="semanticHistory" :history-id="semanticHistoryId" :error="semanticError" :loading="semanticLoading" :busy="semanticBusy" @refresh="loadSemantics" @select-datasource="selectSemanticDatasource" @save="saveSemantic" @edit="editSemantic" @history="loadSemanticHistory" @remove="removeSemantic" @cancel="cancelSemanticEdit" @back="openView('query')" /></template>
