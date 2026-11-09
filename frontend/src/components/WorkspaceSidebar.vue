<script setup>
import { computed } from 'vue'

const props = defineProps({
  view: { type: String, required: true },
  profile: { type: Object, default: null },
  datasourcesCount: { type: Number, default: 0 },
  tasks: { type: Array, default: () => [] },
})

const emit = defineEmits(['navigate', 'resume-task', 'new-query', 'logout'])
const isAdmin = computed(() => props.profile?.roles?.includes('admin'))
const username = computed(() => props.profile?.username || props.profile?.subject || '当前账号')
const roleLabel = computed(() => props.profile?.role || (props.profile?.roles || []).join(' / ') || '用户')
</script>

<template>
  <aside>
    <div class="brand"><span class="mark">Q</span><div><strong>Query Pilot</strong><small>数据查询工作台</small></div></div>
    <nav class="workspace-nav" aria-label="工作区导航">
      <div class="nav-label">工作区资源</div>
      <button v-if="isAdmin" :class="{active:view==='datasources'}" @click="emit('navigate', 'datasources')"><span>▣</span> 数据源 <small>{{datasourcesCount}}</small></button>
      <button :class="{active:view==='tasks' || view==='task'}" @click="emit('navigate', 'tasks')"><span>⌘</span> 数据任务 <small>{{tasks.length}}</small></button>
      <button :class="{active:view==='proactive'}" @click="emit('navigate', 'proactive')"><span>◴</span> 主动分析</button>
      <button v-if="isAdmin" :class="{active:view==='approvals'}" @click="emit('navigate', 'approvals')"><span>✓</span> 审批中心</button>
      <button v-if="isAdmin" :class="{active:view==='users'}" @click="emit('navigate', 'users')"><span>♙</span> 用户管理</button>
      <button v-if="isAdmin" :class="{active:view==='semantics'}" @click="emit('navigate', 'semantics')"><span>≡</span> 语义层</button>
      <button v-if="isAdmin" :class="{active:view==='gateways'}" @click="emit('navigate', 'gateways')"><span>◇</span> 数据网关</button>
      <button v-if="isAdmin" :class="{active:view==='models'}" @click="emit('navigate', 'models')"><span>⚙</span> 模型运营</button>
      <button v-if="isAdmin" :class="{active:view==='mcp'}" @click="emit('navigate', 'mcp')"><span>⇄</span> MCP 集成</button>
      <button v-if="isAdmin" :class="{active:view==='skills'}" @click="emit('navigate', 'skills')"><span>◆</span> Skill 发布</button>
    </nav>
    <button class="new" @click="emit('new-query')">＋ 新建任务</button>
    <div class="side-title">最近任务</div>
    <button v-for="item in tasks.slice(0,8)" :key="item.id" class="history task-link" @click="emit('resume-task', item.id)">
      <span class="history-q">{{item.title || item.question}}</span><span class="task-dot" :class="item.status"></span>
    </button>
    <div class="side-footer">
      <div class="account-avatar">{{username.slice(0,1).toUpperCase()}}</div>
      <button class="account-summary" aria-label="个人资料" @click="emit('navigate', 'profile')"><b>{{username}}</b><small>{{roleLabel}} · {{profile?.tenant_id || 'default'}}</small></button>
      <button class="account-more" @click="emit('navigate', 'sessions')" title="会话管理">•••</button>
      <button class="account-exit" aria-label="退出账号" @click="emit('logout')" title="退出账号">退出</button>
    </div>
  </aside>
</template>
