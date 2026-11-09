<script setup>
import {inject} from 'vue'
import {appContextKey} from '../appContext'
import ModelOperationsPage from '../components/ModelOperationsPage.vue'
const {modelProviders,promptVersions,modelRuntime,providerDraft,providerEditingId,promptDraft,modelOpsError,modelOpsLoading,modelOpsBusy,loadModelOperations,editProvider,cancelProviderEdit,saveProvider,patchProvider,checkProviderHealth,savePromptVersion,activatePrompt,openView}=inject(appContextKey)
</script>
<template><ModelOperationsPage :providers="modelProviders" :prompts="promptVersions" :runtime="modelRuntime" :provider-draft="providerDraft" :provider-editing-id="providerEditingId" :prompt-draft="promptDraft" :error="modelOpsError" :loading="modelOpsLoading" :busy="modelOpsBusy" @refresh="loadModelOperations" @back="openView('query')" @save-provider="saveProvider" @edit-provider="editProvider" @cancel-provider="cancelProviderEdit" @patch-provider="patchProvider" @health="checkProviderHealth" @save-prompt="savePromptVersion" @activate-prompt="activatePrompt" /></template>
