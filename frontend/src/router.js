import { createRouter, createWebHistory } from 'vue-router'
import WorkspaceRoute from './pages/WorkspaceRoute.vue'
import TasksRoute from './pages/TasksRoute.vue'
import TaskDetailRoute from './pages/TaskDetailRoute.vue'
import DataSourcesRoute from './pages/DataSourcesRoute.vue'
import ApprovalsRoute from './pages/ApprovalsRoute.vue'
import UserManagementRoute from './pages/UserManagementRoute.vue'
import ProfileRoute from './pages/ProfileRoute.vue'
import SessionsRoute from './pages/SessionsRoute.vue'
import SemanticsRoute from './pages/SemanticsRoute.vue'
import DataGatewayRoute from './pages/DataGatewayRoute.vue'
import ModelOperationsRoute from './pages/ModelOperationsRoute.vue'
import McpAdminRoute from './pages/McpAdminRoute.vue'
import SkillReleaseRoute from './pages/SkillReleaseRoute.vue'
import ProactiveAnalysisRoute from './pages/ProactiveAnalysisRoute.vue'

export const viewPaths = {
  query: '/workspace',
  tasks: '/tasks',
  task: '/tasks/:id',
  datasources: '/datasources',
  approvals: '/approvals',
  users: '/admin/users',
  semantics: '/admin/semantics',
  gateways: '/admin/gateways',
  models: '/admin/models',
  mcp: '/admin/mcp',
  skills: '/admin/skills',
  proactive: '/proactive-analysis',
  profile: '/profile',
  sessions: '/sessions',
}

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/workspace' },
    { path: '/workspace', component: WorkspaceRoute, meta: { view: 'query' } },
    { path: '/tasks', component: TasksRoute, meta: { view: 'tasks' } },
    { path: '/tasks/:id', component: TaskDetailRoute, meta: { view: 'task' } },
    { path: '/datasources', component: DataSourcesRoute, meta: { view: 'datasources', admin: true } },
    { path: '/approvals', component: ApprovalsRoute, meta: { view: 'approvals', admin: true } },
    { path: '/admin/users', component: UserManagementRoute, meta: { view: 'users', admin: true } },
    { path: '/admin/semantics', component: SemanticsRoute, meta: { view: 'semantics', admin: true } },
    { path: '/admin/gateways', component: DataGatewayRoute, meta: { view: 'gateways', admin: true } },
    { path: '/admin/models', component: ModelOperationsRoute, meta: { view: 'models', admin: true } },
    { path: '/admin/mcp', component: McpAdminRoute, meta: { view: 'mcp', admin: true } },
    { path: '/admin/skills', component: SkillReleaseRoute, meta: { view: 'skills', admin: true } },
    { path: '/proactive-analysis', component: ProactiveAnalysisRoute, meta: { view: 'proactive' } },
    { path: '/profile', component: ProfileRoute, meta: { view: 'profile' } },
    { path: '/sessions', component: SessionsRoute, meta: { view: 'sessions' } },
    { path: '/:pathMatch(.*)*', redirect: '/workspace' },
  ],
})
