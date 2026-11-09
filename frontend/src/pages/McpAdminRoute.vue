<script setup>
import { inject } from 'vue'
import { appContextKey } from '../appContext'
import McpAdminPage from '../components/McpAdminPage.vue'

const {
  mcpServers, mcpDraft, mcpEditingId, mcpError, mcpLoading, mcpBusyId,
  mcpCallDraft, mcpCallResult, mcpCallBusy, loadMcpServers, editMcpServer,
  cancelMcpEdit, saveMcpServer, patchMcpServer, checkMcpHealth, removeMcpServer,
  selectMcpCallServer, invokeMcpTool, openView,
} = inject(appContextKey)
</script>

<template>
  <McpAdminPage
    :servers="mcpServers" :draft="mcpDraft" :editing-id="mcpEditingId" :error="mcpError"
    :loading="mcpLoading" :busy-id="mcpBusyId" :call-draft="mcpCallDraft"
    :call-result="mcpCallResult" :call-busy="mcpCallBusy"
    @refresh="loadMcpServers" @back="openView('query')" @save="saveMcpServer"
    @edit="editMcpServer" @cancel="cancelMcpEdit" @patch="patchMcpServer"
    @health="checkMcpHealth" @remove="removeMcpServer"
    @select-call-server="selectMcpCallServer" @invoke="invokeMcpTool"
  />
</template>
