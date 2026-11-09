<script setup>
import { inject } from 'vue'
import { appContextKey } from '../appContext'
import SessionsPage from '../components/SessionsPage.vue'
const { sessions, openView, revokeDevice, revokeAllDevices } = inject(appContextKey)
</script>
<template><SessionsPage :sessions="sessions" @back="openView('query')" @revoke="revokeDevice" @revoke-all="revokeAllDevices" /></template>
