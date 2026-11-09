<script setup>
import { computed, provide, ref, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { viewPaths } from './router'
import LoginPanel from './components/LoginPanel.vue'
import WorkspaceSidebar from './components/WorkspaceSidebar.vue'
import { appContextKey, skillEvaluationKey } from './appContext'
import { useApiClient } from './composables/useApiClient'
import { useTasks } from './composables/useTasks'
import { useDataSources } from './composables/useDataSources'
import { useApprovals } from './composables/useApprovals'
import { useUserManagement } from './composables/useUserManagement'
import { useAccountManagement } from './composables/useAccountManagement'
import { useAuthentication } from './composables/useAuthentication'
import { useQueryExecution } from './composables/useQueryExecution'
import { useSemantics } from './composables/useSemantics'
import { useDataGateways } from './composables/useDataGateways'
import { useRequestScope } from './composables/useRequestScope'
import { useModelOperations } from './composables/useModelOperations'
import { useMcpServers } from './composables/useMcpServers'
import { useSkillReleases } from './composables/useSkillReleases'
import { useProactiveAnalysis } from './composables/useProactiveAnalysis'
const inspectorOpen = ref(false); const apiOnline = ref(false); const token = ref(''); const copied = ref('')
const route = useRoute(); const router = useRouter(); const view = computed(() => route.meta.view || 'query')
const requestScope = useRequestScope()
const { api, authFetch, csrfToken, refreshAccess } = useApiClient(token)
const { tasks, taskDetail, taskError, taskLoading, loadTasks, loadTask, followUpTask, submitTaskFeedback } = useTasks(api, authFetch, requestScope)
const { datasources, activeDatasource: activeDs, schema: dsSchema, testResults: dsTestResult, loadDatasources, testDatasource: testDs, refreshDatasource: refreshDs, browseDatasource: browseDs, clearDatasources } = useDataSources(api, authFetch)
const { approvals, approvalError, approvalBusy, loadApprovals, decideApproval } = useApprovals(api, authFetch)
const { semantics, semanticDatasource, semanticDraft, semanticEditingId, semanticError, semanticLoading, semanticBusy, semanticHistory, semanticHistoryId, loadSemantics, selectSemanticDatasource, editSemantic, cancelSemanticEdit, saveSemantic, removeSemantic, loadSemanticHistory } = useSemantics(api, authFetch, datasources)
const { gateways, gatewayState, gatewayError, gatewayLoading, loadGateways, refreshHealth: refreshGatewayHealth, testGateway, loadGatewaySchema, selectGatewayTable, previewGateway } = useDataGateways(api, authFetch)
const { modelProviders,promptVersions,modelRuntime,providerDraft,providerEditingId,promptDraft,modelOpsError,modelOpsLoading,modelOpsBusy,loadModelOperations,editProvider,cancelProviderEdit,saveProvider,patchProvider,checkProviderHealth,savePromptVersion,activatePrompt } = useModelOperations(api, authFetch)
const { mcpServers,mcpDraft,mcpEditingId,mcpError,mcpLoading,mcpBusyId,mcpCallDraft,mcpCallResult,mcpCallBusy,loadMcpServers,editMcpServer,cancelMcpEdit,saveMcpServer,patchMcpServer,checkMcpHealth,removeMcpServer,selectMcpCallServer,invokeMcpTool } = useMcpServers(api, authFetch)
const { skillCatalog,skillVersions,skillReleaseDraft,skillCanaryPercent,skillPreviewSubject,skillPreviewResult,skillEvaluationResult,skillQueryQualityResult,skillReleaseError,skillReleaseLoading,skillReleaseBusyId,loadSkillReleases,selectSkill,createSkillDraft,evaluateSkillDraft,evaluateSkillQueryQuality,startSkillCanary,publishSkillVersion,rollbackSkillVersion,previewSkillResolution } = useSkillReleases(api,authFetch)
const { proactiveStatus,proactivePlans,proactiveRuns,proactiveDraft,proactiveError,proactiveLoading,proactiveBusyId,loadProactiveAnalysis,createProactivePlan,transitionProactivePlan,loadProactiveRuns } = useProactiveAnalysis(api,authFetch,datasources)
const { users, newUser, userError, grantUser, grants, newGrant, grantError, grantColumns, policyDatasource, policyRole, rolePolicy, policySchema, policyError, policyBusy, loadUsers, createUser, toggleUser, changeRole, removeUser, resetUserPassword, openGrants, selectGrantDatasource, addGrant, removeGrant, loadRolePolicy, selectPolicyDatasource, selectPolicyRole, togglePolicyTable, togglePolicyColumn, setPolicyMask, saveRolePolicy } = useUserManagement({api, authFetch, datasources, schema: dsSchema, loadDatasources, browseDatasource: browseDs})
const { question, sql, loading, error, result, requireApproval, approvalNotice, exportJob, conversationTaskId, conversationStatus, conversationTitle, conversationTurns, conversationLoading, conversationError, branchDraft, branchOrigin, branchFamily, branchFamilyLoading, onQuestionEdit, beginBranch, cancelBranchDraft, cancelQuery, query, resumeConversation, reset: resetQuery, usePrompt, startExport, downloadExport } = useQueryExecution({api, authFetch, activeDatasource: activeDs, loadTasks, onUnauthorized: handleUnauthorized, onTaskStarted: syncConversationRoute, requestScope})
const { username, password, profile, displayName, profileError, profileMsg, loginError, loginLoading, initialize, login, logout, updateProfile } = useAuthentication({api, token, authFetch, csrfToken, refreshAccess, router, onAuthenticated: loadAuthenticatedData, onClear: () => { requestScope.invalidate(); clearDatasources(); resetQuery() }})
const { sessions, password: pwd, passwordError: pwdError, passwordMessage: pwdMsg, changePassword, loadDevices, revokeDevice, revokeAllDevices } = useAccountManagement({api, authFetch, logout, queryError: error})
const canExport = computed(() => (profile.value?.roles || []).some(r => ['admin', 'analyst'].includes(r)))
const activeDsName = computed(() => datasources.value.find(d => d.id === activeDs.value)?.name || activeDs.value || '—')
async function checkApi() { try { const response = await fetch(`${api}/health`); apiOnline.value = response.ok } catch { apiOnline.value = false } }
checkApi()
onMounted(initialize)
async function loadAuthenticatedData(identity){ if(route.meta.admin && !identity?.roles?.includes('admin')) await router.replace('/workspace'); await loadDatasources(); await loadTasks(); if(view.value!=='tasks') await loadViewData(view.value) }
async function loadViewData(name){ if(name==='users') { await loadUsers(); await loadRolePolicy() } if(name==='sessions') await loadDevices(); if(name==='datasources') await loadDatasources(); if(name==='approvals') await loadApprovals(); if(name==='semantics') await loadSemantics(); if(name==='gateways') await loadGateways(); if(name==='models') await loadModelOperations(); if(name==='mcp') await loadMcpServers(); if(name==='skills') await loadSkillReleases(); if(name==='proactive') await loadProactiveAnalysis(); if(name==='tasks') await loadTasks(); if(name==='task') await loadTask(route.params.id); if(name==='query' && route.query.task) await resumeConversation(String(route.query.task)) }
async function openView(name, params={}){ const pattern=viewPaths[name] || viewPaths.query; const path=pattern.replace(':id', encodeURIComponent(params.id || '')); await router.push(path) }
watch(() => route.fullPath, async () => { if(profile.value && route.meta.admin && !profile.value?.roles?.includes('admin')) { await router.replace('/workspace'); return } if(profile.value) await loadViewData(view.value) })
async function handleUnauthorized(){ await logout(false); loginError.value='登录已过期，请重新登录' }
provide(skillEvaluationKey, { skillEvaluationResult, skillQueryQualityResult, evaluateSkillDraft, evaluateSkillQueryQuality })
async function syncConversationRoute(taskId){ if(taskId && view.value==='query' && route.query.task !== taskId) await router.replace({path:'/workspace', query:{task:taskId}}) }
async function resumeTaskConversation(taskId){ await openView('query'); await router.replace({path:'/workspace', query:{task:taskId}}); await resumeConversation(taskId) }
function newQuery(){ resetQuery(); router.push('/workspace') }
async function copyText(text){ try { await navigator.clipboard.writeText(String(text ?? '')); copied.value='已复制'; setTimeout(()=>{ if(copied.value==='已复制') copied.value='' },1500) } catch { copied.value='复制失败'; setTimeout(()=>copied.value='',1500) } }
provide(appContextKey, { question, sql, loading, error, result, inspectorOpen, apiOnline, requireApproval, approvalNotice, exportJob, conversationTaskId, conversationStatus, conversationTitle, conversationTurns, conversationLoading, conversationError, branchDraft, branchOrigin, branchFamily, branchFamilyLoading, api, activeDs, activeDsName, canExport, datasources, dsSchema, dsTestResult, tasks, taskDetail, taskError, taskLoading, approvals, approvalError, approvalBusy, semantics, semanticDatasource, semanticDraft, semanticEditingId, semanticError, semanticLoading, semanticBusy, semanticHistory, semanticHistoryId, gateways, gatewayState, gatewayError, gatewayLoading, modelProviders,promptVersions,modelRuntime,providerDraft,providerEditingId,promptDraft,modelOpsError,modelOpsLoading,modelOpsBusy, mcpServers,mcpDraft,mcpEditingId,mcpError,mcpLoading,mcpBusyId,mcpCallDraft,mcpCallResult,mcpCallBusy, skillCatalog,skillVersions,skillReleaseDraft,skillCanaryPercent,skillPreviewSubject,skillPreviewResult,skillReleaseError,skillReleaseLoading,skillReleaseBusyId, proactiveStatus,proactivePlans,proactiveRuns,proactiveDraft,proactiveError,proactiveLoading,proactiveBusyId, users, newUser, userError, grantUser, grants, newGrant, grantError, grantColumns, policyDatasource, policyRole, rolePolicy, policySchema, policyError, policyBusy, profile, displayName, profileError, profileMsg, sessions, pwd, pwdError, pwdMsg, onQuestionEdit, beginBranch, cancelBranchDraft, query, cancelQuery, resumeConversation, resumeTaskConversation, openView, newQuery, usePrompt, startExport, downloadExport, copyText, loadTask, followUpTask, submitTaskFeedback, testDs, refreshDs, browseDs, loadApprovals, decideApproval, loadSemantics, selectSemanticDatasource, editSemantic, cancelSemanticEdit, saveSemantic, removeSemantic, loadSemanticHistory, loadGateways, refreshGatewayHealth, testGateway, loadGatewaySchema, selectGatewayTable, previewGateway, loadModelOperations,editProvider,cancelProviderEdit,saveProvider,patchProvider,checkProviderHealth,savePromptVersion,activatePrompt, loadMcpServers,editMcpServer,cancelMcpEdit,saveMcpServer,patchMcpServer,checkMcpHealth,removeMcpServer,selectMcpCallServer,invokeMcpTool, loadSkillReleases,selectSkill,createSkillDraft,startSkillCanary,publishSkillVersion,rollbackSkillVersion,previewSkillResolution, loadProactiveAnalysis,createProactivePlan,transitionProactivePlan,loadProactiveRuns, createUser, toggleUser, changeRole, resetUserPassword, openGrants, removeUser, selectGrantDatasource, addGrant, removeGrant, selectPolicyDatasource, selectPolicyRole, togglePolicyTable, togglePolicyColumn, setPolicyMask, saveRolePolicy, changePassword, revokeDevice, revokeAllDevices, updateProfile })
</script>
<template>
  <LoginPanel v-if="!token" v-model:username="username" v-model:password="password" :error="loginError" :loading="loginLoading" @submit="login" />
  <div v-else class="shell" :class="{compact: view !== 'query', 'console-open': inspectorOpen && view === 'query'}">
    <WorkspaceSidebar :view="view" :profile="profile" :datasources-count="datasources.length" :tasks="tasks" @navigate="openView" @resume-task="resumeTaskConversation" @new-query="newQuery" @logout="logout()" />

    <RouterView />

    <div v-if="copied" class="toast">{{copied}}</div>
  </div>
</template>
