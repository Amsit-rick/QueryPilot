<script setup>
import { inject } from 'vue'
import { appContextKey } from '../appContext'
import DataGatewayPage from '../components/DataGatewayPage.vue'
const { gateways,gatewayState,gatewayError,gatewayLoading,loadGateways,refreshGatewayHealth,testGateway,loadGatewaySchema,selectGatewayTable,previewGateway,openView } = inject(appContextKey)
</script>
<template><DataGatewayPage :gateways="gateways" :states="gatewayState" :error="gatewayError" :loading="gatewayLoading" @refresh="loadGateways" @health="refreshGatewayHealth" @test="testGateway" @schema="loadGatewaySchema" @select-table="selectGatewayTable" @preview="previewGateway" @back="openView('query')" /></template>
