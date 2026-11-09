<script setup>
import { inject } from 'vue'
import { appContextKey } from '../appContext'
import ProfilePage from '../components/ProfilePage.vue'
const { pwd, profile, displayName, profileError, profileMsg, pwdError, pwdMsg, openView, changePassword, updateProfile } = inject(appContextKey)
</script>
<template><ProfilePage v-model:password="pwd" v-model:display-name="displayName" :profile="profile" :profile-error="profileError" :profile-message="profileMsg" :error="pwdError" :message="pwdMsg" @back="openView('query')" @change-password="changePassword" @update-profile="updateProfile" /></template>
