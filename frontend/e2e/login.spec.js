// 前端登录流程端到端测试（Playwright）
// 运行前提：
//   1) 后端已启动（默认 127.0.0.1:8000），且已执行 V003~V027 迁移并初始化管理员
//   2) 前端 dev 服务已启动（npm run dev，默认 127.0.0.1:5173）
//   3) npx playwright install chromium（首次）
//   4) playwright.config.js 从根目录 .env 读取 LOCAL_ADMIN_USERNAME / LOCAL_ADMIN_PASSWORD，
//      并以 TEST_USERNAME / TEST_PASSWORD 注入；不要再写死演示密码。
import { test, expect } from '@playwright/test'

const LOGIN_USER = process.env.TEST_USERNAME || 'admin'
const LOGIN_PASSWORD = process.env.TEST_PASSWORD || ''

async function mockAuthenticatedApp(page, tasks = []) {
  let authenticated = false
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/auth/login')) {
      authenticated = true
      return route.fulfill({json: {access_token: 'route-token'}})
    }
    if (path.endsWith('/auth/refresh')) return authenticated
      ? route.fulfill({json: {access_token: 'route-token'}})
      : route.fulfill({status: 401, json: {detail: 'not logged in'}})
    if (path.endsWith('/auth/me')) return route.fulfill({json: {username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true}})
    if (path.endsWith('/data-sources')) return route.fulfill({json: [{id: 'sqlite-demo', name: 'SQLite 演示库', backend: 'sqlite', readonly: true}]})
    if (path.endsWith('/tasks')) return route.fulfill({json: tasks})
    if (path.endsWith('/query')) return route.fulfill({json: {answer: '查询完成', columns: ['region', 'amount'], rows: [['华东', 100]], row_count: 1, sql: 'SELECT region, amount FROM sales LIMIT 1', trace_id: 'trace-login-e2e'}})
    if (path.endsWith('/auth/logout')) return route.fulfill({json: {ok: true}})
    return route.fulfill({status: 404, json: {detail: 'not mocked'}})
  })
}

test('登录成功进入工作台，退出回到登录页', async ({ page }) => {
  await mockAuthenticatedApp(page)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Query Pilot' })).toBeVisible()

  await page.getByPlaceholder('用户名').fill(LOGIN_USER)
  await page.getByPlaceholder('密码').fill(LOGIN_PASSWORD)
  await Promise.all([
    page.waitForResponse(response => response.url().includes('/api/v1/auth/login')),
    page.getByRole('button', { name: '登录' }).click(),
  ])

  await expect(page.getByRole('heading', { name: '你的数据，直接问。' })).toBeVisible()

  await page.getByRole('button', { name: '退出账号' }).click()
  await expect(page.getByRole('heading', { name: 'Query Pilot' })).toBeVisible()
})

test('错误密码展示错误提示', async ({ page }) => {
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/auth/login')) return route.fulfill({status: 401, json: {detail: '用户名或密码错误'}})
    if (path.endsWith('/auth/refresh')) return route.fulfill({status: 401, json: {detail: 'not logged in'}})
    return route.fulfill({status: 404, json: {detail: 'not mocked'}})
  })
  await page.goto('/')
  await page.getByPlaceholder('用户名').fill('nobody')
  await page.getByPlaceholder('密码').fill('wrong-password-123')
  await page.getByRole('button', { name: '登录' }).click()

  await expect(page.locator('.login .error')).toContainText(/用户名或密码错误|登录失败/)
})

test('登录后可执行查询并显示结果区域', async ({ page }) => {
  await mockAuthenticatedApp(page)
  await page.goto('/')
  await page.getByPlaceholder('用户名').fill(LOGIN_USER)
  await page.getByPlaceholder('密码').fill(LOGIN_PASSWORD)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('heading', { name: '你的数据，直接问。' })).toBeVisible()
  await page.getByPlaceholder(/查询各区域销售额/).fill('查询各区域销售额')
  await page.getByRole('button', { name: /执行查询/ }).click()
  await expect(page.locator('.result, .error')).toBeVisible({ timeout: 15000 })
})

test('可选择授权数据源并将选择结果用于查询', async ({ page }) => {
  let queryPayload
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/auth/login')) return route.fulfill({ json: { access_token: 'ui-token' } })
    if (path.endsWith('/auth/me')) return route.fulfill({ json: { username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true } })
    if (path.endsWith('/sessions')) return route.fulfill({ json: [] })
    if (path.endsWith('/tasks')) return route.fulfill({ json: [] })
    if (path.endsWith('/data-sources')) return route.fulfill({ json: [
      { id: 'sqlite-demo', name: 'SQLite 演示库', backend: 'sqlite', readonly: true },
      { id: 'mysql-main', name: 'MySQL 生产库', backend: 'mysql', readonly: true },
      { id: 'oracle-main', name: 'Oracle 19c 业务库', backend: 'oracle', readonly: true },
    ] })
    if (path.endsWith('/query')) {
      queryPayload = request.postDataJSON()
      return route.fulfill({ json: { answer: '查询完成', columns: ['value'], rows: [[1]], row_count: 1, sql: 'SELECT 1', trace_id: 'trace-ds' } })
    }
    return route.fulfill({ status: 404, json: { detail: 'not mocked' } })
  })

  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('QueryPilot@20040508')
  await page.getByRole('button', { name: '登录' }).click()

  const selector = page.getByRole('combobox', { name: '选择数据源' })
  await expect(selector.locator('option')).toHaveText(['SQLite 演示库', 'MySQL 生产库', 'Oracle 19c 业务库'])
  await selector.selectOption('oracle-main')
  await page.getByPlaceholder(/查询各区域销售额/).fill('查询销售额')
  await page.getByRole('button', { name: '执行查询' }).click()

  await expect.poll(() => queryPayload?.datasource).toBe('oracle-main')
  await expect(page.locator('.workspace-chip')).toHaveText('Oracle 19c 业务库')
  await expect(page.getByRole('heading', { name: '查询完成' })).toBeVisible()
  await expect(page.locator('.result')).toContainText('1 行')
})

test('页面导航使用真实路由且刷新后保持', async ({ page }) => {
  await mockAuthenticatedApp(page)
  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('heading', { name: '你的数据，直接问。' })).toBeVisible()

  await page.getByRole('button', { name: '个人资料' }).click()
  await expect(page).toHaveURL(/\/profile$/)
  await expect(page.getByRole('heading', { name: '个人资料' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: '个人资料' })).toBeVisible()

  await page.getByRole('button', { name: '返回查询' }).click()
  await expect(page).toHaveURL(/\/workspace$/)
})

test('数据任务使用独立列表路由', async ({ page }) => {
  await mockAuthenticatedApp(page)
  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button', { name: '登录' }).click()
  await page.getByRole('button', { name: '数据任务' }).click()

  await expect(page).toHaveURL(/\/tasks$/)
  await expect(page.getByRole('heading', { name: '数据任务', exact: true })).toBeVisible()
  await expect(page.locator('.task-list, .task-empty')).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: '数据任务', exact: true })).toBeVisible()
})

test('访问令牌过期后自动刷新并重试任务请求', async ({ page }) => {
  let authenticated = false
  let allowRefresh = false
  let expireNextTaskRequest = false
  let taskCalls = 0
  let refreshCalls = 0
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/auth/refresh')) {
      refreshCalls += 1
      if (!allowRefresh) return route.fulfill({status: 401, json: {detail: 'not logged in'}})
      authenticated = true
      return route.fulfill({json: {access_token: `refreshed-${refreshCalls}`}})
    }
    if (path.endsWith('/auth/login')) {
      authenticated = true
      allowRefresh = true
      return route.fulfill({json: {access_token: 'initial-token'}})
    }
    if (path.endsWith('/auth/me')) return route.fulfill({json: {username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true}})
    if (path.endsWith('/data-sources')) return route.fulfill({json: [{id: 'sqlite-demo', name: 'SQLite 演示库', backend: 'sqlite', readonly: true}]})
    if (path.endsWith('/tasks')) {
      taskCalls += 1
      if (expireNextTaskRequest) {
        expireNextTaskRequest = false
        authenticated = false
        return route.fulfill({status: 401, json: {detail: 'token expired'}})
      }
      return route.fulfill({status: authenticated ? 200 : 401, json: authenticated ? [] : {detail: 'unauthorized'}})
    }
    return route.fulfill({status: 404, json: {detail: 'not mocked'}})
  })
  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill(LOGIN_USER)
  await page.getByPlaceholder('密码').fill(LOGIN_PASSWORD)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('heading', { name: '你的数据，直接问。' })).toBeVisible()

  expireNextTaskRequest = true
  await page.getByRole('button', { name: '数据任务' }).click()
  await expect(page).toHaveURL(/\/tasks$/)
  await expect(page.getByRole('heading', { name: '数据任务', exact: true })).toBeVisible()
  await expect.poll(() => refreshCalls).toBeGreaterThanOrEqual(1)
  await expect.poll(() => taskCalls).toBeGreaterThanOrEqual(3)
})

test('管理员可通过界面维护用户数据权限', async ({ page }) => {
  let grants = []
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    if (path.endsWith('/auth/login')) return route.fulfill({ json: { access_token: 'ui-token' } })
    if (path.endsWith('/auth/me')) return route.fulfill({ json: { username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true } })
    if (path.endsWith('/sessions')) return route.fulfill({ json: [] })
    if (path.endsWith('/tasks')) return route.fulfill({ json: [] })
    if (path.endsWith('/data-sources')) return route.fulfill({ json: [{ id: 'sqlite-demo', name: 'SQLite Demo', backend: 'sqlite' }] })
    if (path.endsWith('/schema')) return route.fulfill({ json: { sales: ['region', 'amount'] } })
    if (path === '/api/v1/admin/users') return route.fulfill({ json: [{ id: 2, username: 'analyst', role: 'analyst', is_active: true, created_at: '2026-09-09T00:00:00' }] })
    if (path.endsWith('/grants') && request.method() === 'GET') return route.fulfill({ json: grants })
    if (path.endsWith('/grants') && request.method() === 'POST') { grants = [{ id: 11, datasource_id: 'sqlite-demo', scope_column: 'region', scope_value: '华东' }]; return route.fulfill({ json: { id: 11 } }) }
    if (path.includes('/admin/grants/') && request.method() === 'DELETE') { grants = []; return route.fulfill({ json: { status: 'ok' } }) }
    return route.fulfill({ status: 404, json: { detail: 'not mocked' } })
  })

  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('QueryPilot@20040508')
  await page.getByRole('button', { name: '登录' }).click()
  await page.getByRole('button', { name: '用户管理' }).click()
  await expect(page).toHaveURL(/\/admin\/users$/)
  await page.getByRole('button', { name: '数据权限' }).click()
  await expect(page.getByText('数据权限：analyst')).toBeVisible()
  await page.getByPlaceholder('授权值，如 华东').fill('华东')
  await page.getByRole('button', { name: '添加授权' }).click()
  await expect(page.getByText('华东')).toBeVisible()
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: '移除' }).click()
  await expect(page.getByText('该用户暂无数据授权')).toBeVisible()
})
