<script setup>
import { inject } from 'vue'
import { appContextKey } from '../appContext'
import ApprovalsPage from '../components/ApprovalsPage.vue'
const { approvals, approvalError, approvalBusy, loadApprovals, openView, decideApproval } = inject(appContextKey)
</script>
<template><ApprovalsPage :approvals="approvals" :error="approvalError" :busy-id="approvalBusy" @refresh="loadApprovals" @back="openView('query')" @decide="decideApproval" /></template>
