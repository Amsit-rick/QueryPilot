import { test, expect } from '@playwright/test'

const API_BASE = process.env.E2E_API_BASE_URL || 'http://127.0.0.1:8000'
const LOGIN_USER = process.env.TEST_USERNAME || 'admin'
const LOGIN_PASSWORD = process.env.TEST_PASSWORD || ''
const USER_PREFIX = 'e2e_viewer_'
const MCP_PREFIX = 'E2E MCP '

test.describe.configure({ mode: 'serial', timeout: 60_000 })

function runId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

async function loginThroughUi(page) {
  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill(LOGIN_USER)
  await page.getByPlaceholder('密码').fill(LOGIN_PASSWORD)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('heading', { name: '你的数据，直接问。' })).toBeVisible()
}

async function apiHeaders() {
  const response = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: LOGIN_USER, password: LOGIN_PASSWORD }),
  })
  const body = await response.json()
  if (!response.ok) {
    throw new Error(`API login failed (${response.status}): ${body.detail || 'unknown error'}`)
  }
  return { Authorization: `Bearer ${body.access_token}` }
}

async function jsonRequest(method, path, headers, data) {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { ...headers, ...(data === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: data === undefined ? undefined : JSON.stringify(data),
  })
  const body = response.status === 204 ? null : await response.json().catch(() => null)
  return { response, body }
}

async function deleteUsersByPrefix(headers, prefix = USER_PREFIX) {
  const { response, body } = await jsonRequest('GET', '/api/v1/admin/users', headers)
  if (!response.ok || !Array.isArray(body)) return
  for (const user of body.filter(item => String(item.username).startsWith(prefix))) {
    await jsonRequest('DELETE', `/api/v1/admin/users/${user.id}`, headers)
  }
}

async function deleteMcpServersByPrefix(headers, prefix = MCP_PREFIX) {
  const { response, body } = await jsonRequest('GET', '/api/v1/mcp-servers', headers)
  if (!response.ok || !Array.isArray(body)) return
  for (const server of body.filter(item => String(item.name).startsWith(prefix))) {
    await jsonRequest('DELETE', `/api/v1/mcp-servers/${server.id}`, headers)
  }
}

async function datasourceWithRegion(headers) {
  const sourcesResponse = await fetch(`${API_BASE}/api/v1/data-sources`, { headers })
  expect(sourcesResponse.ok).toBeTruthy()
  const sources = await sourcesResponse.json()
  for (const source of sources) {
    const schemaResponse = await fetch(`${API_BASE}/api/v1/data-sources/${encodeURIComponent(source.id)}/schema`, { headers })
    if (!schemaResponse.ok) continue
    const schema = await schemaResponse.json()
    if (Object.values(schema).some(columns => Array.isArray(columns) && columns.includes('region'))) return source.id
  }
  throw new Error('No authorized datasource exposes a region column for the live E2E grant test.')
}

test.afterEach(async () => {
  const headers = await apiHeaders()
  await deleteUsersByPrefix(headers)
  await deleteMcpServersByPrefix(headers)
})

test('真实管理员可创建用户、维护行级授权并清理账号', async ({ page }) => {
  const suffix = runId()
  const username = `${USER_PREFIX}${suffix}`.slice(0, 60)
  const password = `E2e-${crypto.randomUUID()}-Aa1!`
  const headers = await apiHeaders()
  const datasourceId = await datasourceWithRegion(headers)

  await deleteUsersByPrefix(headers)
  await loginThroughUi(page)
  await page.getByRole('button', { name: '用户管理' }).click()
  await expect(page).toHaveURL(/\/admin\/users$/)

  await page.getByPlaceholder('用户名').fill(username)
  await page.getByPlaceholder('密码（至少 12 位）').fill(password)
  await page.locator('.user-create select').first().selectOption('viewer')
  await page.getByRole('button', { name: '创建用户' }).click()

  const userRow = page.getByRole('row').filter({ hasText: username })
  await expect(userRow).toContainText('viewer')
  await userRow.getByRole('button', { name: '数据权限' }).click()

  const grantCard = page.locator('.card').filter({ hasText: `数据权限：${username}` })
  await expect(grantCard).toBeVisible()
  await grantCard.locator('select').nth(0).selectOption(datasourceId)
  await grantCard.locator('select').nth(1).selectOption('region')
  await grantCard.getByPlaceholder('授权值，如 华东').fill('华东')
  await grantCard.getByRole('button', { name: '添加授权' }).click()
  await expect(grantCard.getByRole('row').filter({ hasText: '华东' })).toBeVisible()

  page.once('dialog', dialog => dialog.accept())
  await grantCard.getByRole('button', { name: '移除' }).click()
  await expect(grantCard.getByText('该用户暂无数据授权')).toBeVisible()

  page.once('dialog', dialog => dialog.accept())
  await userRow.getByRole('button', { name: '删除' }).click()
  await expect(page.getByRole('row').filter({ hasText: username })).toHaveCount(0)
})

test('真实 MCP 注册可创建、编辑、停启并删除且不保存明文凭据', async ({ page }) => {
  const prefix = `${MCP_PREFIX}${runId()}`
  const updatedName = `${prefix} Updated`
  const headers = await apiHeaders()

  await deleteMcpServersByPrefix(headers)
  await loginThroughUi(page)
  await page.getByRole('button', { name: 'MCP 集成' }).click()
  await expect(page).toHaveURL(/\/admin\/mcp$/)

  await page.getByLabel('名称', { exact: true }).fill(prefix)
  await page.getByLabel('Endpoint').fill('https://mcp.example.com/mcp')
  await page.getByLabel('凭据环境变量').fill('E2E_MCP_TOKEN')
  await page.getByLabel('允许工具').fill('schema.list')
  await page.getByRole('button', { name: '注册 Server' }).click()

  let serverCard = page.getByRole('article').filter({ hasText: prefix })
  await expect(serverCard).toContainText('E2E_MCP_TOKEN')
  await expect(serverCard).not.toContainText(/token-[A-Za-z0-9]/)

  await serverCard.getByRole('button', { name: '编辑' }).click()
  await page.getByLabel('名称', { exact: true }).fill(updatedName)
  await page.getByRole('button', { name: '保存修改' }).click()
  serverCard = page.getByRole('article').filter({ hasText: updatedName })
  await expect(serverCard).toBeVisible()

  await serverCard.getByRole('button', { name: '停用' }).click()
  await expect(serverCard.getByText('已停用', { exact: true })).toBeVisible()
  await serverCard.getByRole('button', { name: '启用' }).click()
  await expect(serverCard.getByText('未检查', { exact: true })).toBeVisible()

  page.once('dialog', dialog => dialog.accept())
  await serverCard.getByRole('button', { name: '删除' }).click()
  await expect(page.getByRole('article').filter({ hasText: updatedName })).toHaveCount(0)
})

test('真实 Skill 目录支持无持久化的路由评测与稳定桶预览', async ({ page }) => {
  const headers = await apiHeaders()
  const before = await fetch(`${API_BASE}/api/v1/skills/admin/versions`, { headers })
  expect(before.ok).toBeTruthy()
  const versionCount = (await before.json()).length

  await loginThroughUi(page)
  await page.getByRole('button', { name: 'Skill 发布' }).click()
  await expect(page).toHaveURL(/\/admin\/skills$/)
  await expect.poll(() => page.getByLabel('目标 Skill').locator('option').count()).toBeGreaterThan(0)

  await page.getByLabel('新版本号').fill('99.99.99')
  await page.getByRole('button', { name: '对比路由效果' }).click()
  await expect(page.getByText('固定集路由评测')).toBeVisible()

  await page.getByLabel('预览用户').fill(`e2e-viewer-${runId()}`)
  await page.getByRole('button', { name: '预览命中' }).click()
  await expect(page.locator('.skill-preview-result')).toContainText(/稳定桶.*命中/s)

  const after = await fetch(`${API_BASE}/api/v1/skills/admin/versions`, { headers })
  expect(after.ok).toBeTruthy()
  expect((await after.json()).length).toBe(versionCount)
})

test('真实审批会重新校验只读 SQL、执行并在刷新后恢复任务结果', async ({ page }) => {
  const headers = await apiHeaders()
  const datasourceId = await datasourceWithRegion(headers)
  const question = `[E2E审批] 查询区域金额 ${runId()}`
  const { response: queryResponse, body: pending } = await jsonRequest('POST', '/api/v1/query', headers, {
    question,
    client_request_id: `e2e-approval-${crypto.randomUUID()}`,
    datasource: datasourceId,
    sql: 'SELECT region, amount FROM sales LIMIT 2',
    require_approval: true,
  })
  expect(queryResponse.ok).toBeTruthy()
  expect(pending.status).toBe('approval_required')

  await loginThroughUi(page)
  await page.getByRole('button', { name: '审批中心' }).click()
  await expect(page).toHaveURL(/\/approvals$/)

  const approvalCard = page.locator('.approval-item').filter({ hasText: question })
  await expect(approvalCard).toContainText('待审批')
  await approvalCard.getByRole('button', { name: '批准并执行' }).click()
  await expect(approvalCard).toContainText('已批准')
  await expect(approvalCard).toContainText('执行完成：2 行')

  await page.goto(`/tasks/${pending.task_id}`)
  await expect(page.getByRole('heading', { name: question })).toBeVisible()
  await expect(page.locator('.task-state')).toContainText('已完成')
  await expect(page.getByRole('heading', { name: '查询结果' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: question })).toBeVisible()
  await expect(page.locator('.task-state')).toContainText('已完成')
})
