<template>
  <div class="login">
    <form @submit.prevent="$emit('submit')">
      <h1>Query Pilot</h1>
      <p>登录数据查询工作台</p>
      <input :value="username" @input="$emit('update:username', $event.target.value)" autocomplete="username" placeholder="用户名">
      <input :value="password" @input="$emit('update:password', $event.target.value)" type="password" autocomplete="current-password" placeholder="密码">
      <button class="run" type="submit" :disabled="loading">{{ loading ? '登录中…' : '登录' }}</button>
      <div v-if="error" class="error">{{ error }}</div>
    </form>
  </div>
</template>

<script setup>
defineProps({
  username: { type: String, default: '' },
  password: { type: String, default: '' },
  error: { type: String, default: '' },
  loading: { type: Boolean, default: false },
})
defineEmits(['submit', 'update:username', 'update:password'])
</script>
