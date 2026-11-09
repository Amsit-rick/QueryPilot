import { test, expect } from '@playwright/test'

test('管理员可以配置、检查并诊断外部 MCP Server', async ({ page }) => {
  let servers = []
  let createBody = null
  let invokeBody = null
  const patchBodies = []

  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    if (path.endsWith('/auth/refresh')) return route.fulfill({ status: 401, json: { detail: 'not logged in' } })
    if (path.endsWith('/auth/login')) return route.fulfill({ json: { access_token: 'ui-token' } })
    if (path.endsWith('/auth/me')) return route.fulfill({ json: { username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true } })
    if (path.endsWith('/data-sources')) return route.fulfill({ json: [{ id: 'sqlite-demo', name: 'SQLite Demo', backend: 'sqlite' }] })
    if (path.endsWith('/tasks')) return route.fulfill({ json: [] })

    if (path.endsWith('/mcp-servers') && request.method() === 'GET') return route.fulfill({ json: servers })
    if (path.endsWith('/mcp-servers') && request.method() === 'POST') {
      createBody = JSON.parse(request.postData())
      servers = [{
        id: 'mcp-1', tenant_id: 'default', ...createBody,
        credential_configured: false, health_status: 'unchecked', last_latency_ms: 0,
        last_error_code: '', discovered_tools: [], last_checked_at: null,
        created_at: '2026-11-03T10:00:00Z', updated_at: '2026-11-03T10:00:00Z',
      }]
      return route.fulfill({ json: servers[0] })
    }
    if (path.endsWith('/mcp-servers/mcp-1/health')) {
      servers = [{
        ...servers[0], credential_configured: true, health_status: 'healthy',
        last_latency_ms: 18, discovered_tools: ['schema.list', 'report.read'],
        last_checked_at: '2026-11-03T10:05:00Z',
      }]
      return route.fulfill({ json: servers[0] })
    }
    if (path.endsWith('/mcp-servers/mcp-1/invoke')) {
      invokeBody = JSON.parse(request.postData())
      return route.fulfill({ json: { server_id: 'mcp-1', tool_name: invokeBody.tool_name, duration_ms: 12, result: { content: [{ type: 'text', text: 'ok' }], isError: false, resultType: 'complete' } } })
    }
    if (path.endsWith('/mcp-servers/mcp-1') && request.method() === 'PATCH') {
      const patchBody = JSON.parse(request.postData())
      patchBodies.push(patchBody)
      servers = [{ ...servers[0], ...patchBody }]
      return route.fulfill({ json: servers[0] })
    }
    if (path.endsWith('/mcp-servers/mcp-1') && request.method() === 'DELETE') {
      servers = []
      return route.fulfill({ status: 204, body: '' })
    }
    return route.fulfill({ status: 404, json: { detail: 'not mocked' } })
  })

  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button', { name: '登录' }).click()
  await page.getByRole('button', { name: 'MCP 集成' }).click()

  await expect(page).toHaveURL(/\/admin\/mcp$/)
  await expect(page.getByRole('heading', { name: 'MCP Server 管理' })).toBeVisible()
  await page.getByLabel('名称', { exact: true }).fill('Finance MCP')
  await page.getByLabel('Endpoint').fill('https://mcp.example.com/mcp')
  await page.getByLabel('凭据环境变量').fill('FINANCE_MCP_TOKEN')
  await page.getByLabel('允许工具').fill('schema.list\nreport.read')
  await page.getByLabel('请求超时（秒）').fill('8')
  await page.getByLabel('最大并发').fill('3')
  await page.getByLabel('每日调用上限').fill('200')
  await page.getByRole('button', { name: '注册 Server' }).click()

  await expect.poll(() => createBody).not.toBeNull()
  expect(createBody).toMatchObject({
    name: 'Finance MCP',
    auth_env_ref: 'FINANCE_MCP_TOKEN',
    allowed_tools: ['schema.list', 'report.read'],
    request_timeout_seconds: 8,
    max_concurrency: 3,
    daily_call_limit: 200,
    read_only_only: true,
  })
  expect(createBody).not.toHaveProperty('token')
  expect(createBody).not.toHaveProperty('api_key')
  await expect(page.getByRole('article').getByText('Finance MCP')).toBeVisible()

  await page.getByRole('button', { name: '编辑' }).click()
  await page.getByLabel('名称', { exact: true }).fill('Finance MCP Updated')
  await page.getByRole('button', { name: '保存修改' }).click()
  await expect.poll(() => patchBodies[0]?.name).toBe('Finance MCP Updated')
  expect(patchBodies[0]).not.toHaveProperty('token')
  await expect(page.getByRole('article').getByText('Finance MCP Updated')).toBeVisible()

  await page.getByRole('button', { name: '健康检查' }).click()
  await expect(page.getByText('健康', { exact: true })).toBeVisible()
  await expect(page.getByRole('article').getByText('report.read', { exact: true }).first()).toBeVisible()

  await page.getByLabel('诊断工具').selectOption('report.read')
  await page.getByLabel('诊断参数 JSON').fill('[]')
  await page.getByRole('button', { name: '调用只读工具' }).click()
  await expect(page.getByRole('alert')).toContainText('诊断参数必须是 JSON 对象')
  expect(invokeBody).toBeNull()
  await page.getByLabel('诊断参数 JSON').fill('{"region":"华东"}')
  await page.getByRole('button', { name: '调用只读工具' }).click()
  await expect.poll(() => invokeBody).toEqual({ tool_name: 'report.read', arguments: { region: '华东' } })
  await expect(page.getByText('调用成功')).toBeVisible()

  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 })
    await expect(page.locator('.mcp-admin-page')).toBeVisible()
    await page.locator('.mcp-admin-page').evaluate(element => { element.scrollTop = 0 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    if (process.env.E2E_CAPTURE_MCP === '1') {
      await page.screenshot({ path: `test-results/mcp-admin-${width}.png`, fullPage: true })
    }
  }
  await page.setViewportSize({ width: 1280, height: 900 })

  await page.getByRole('button', { name: '停用' }).click()
  await expect.poll(() => patchBodies.at(-1)).toEqual({ enabled: false })
  await expect(page.getByText('已停用', { exact: true })).toBeVisible()
  await expect(page.getByLabel('诊断工具')).toBeDisabled()

  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: '删除' }).click()
  await expect(page.getByText('暂无外部 MCP Server。')).toBeVisible()

})

test('非管理员不能进入 MCP 管理页面', async ({ page }) => {
  let mcpRequests = 0
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/auth/refresh')) return route.fulfill({ status: 401, json: { detail: 'not logged in' } })
    if (path.endsWith('/auth/login')) return route.fulfill({ json: { access_token: 'analyst-token' } })
    if (path.endsWith('/auth/me')) return route.fulfill({ json: { username: 'analyst', roles: ['analyst'], tenant_id: 'default', is_active: true } })
    if (path.endsWith('/data-sources')) return route.fulfill({ json: [{ id: 'sqlite-demo', name: 'SQLite Demo', backend: 'sqlite' }] })
    if (path.endsWith('/tasks')) return route.fulfill({ json: [] })
    if (path.includes('/mcp-servers')) {
      mcpRequests += 1
      return route.fulfill({ status: 403, json: { detail: 'forbidden' } })
    }
    return route.fulfill({ status: 404, json: { detail: 'not mocked' } })
  })

  await page.goto('/admin/mcp')
  await page.getByPlaceholder('用户名').fill('analyst')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button', { name: '登录' }).click()

  await expect(page).toHaveURL(/\/workspace$/)
  await expect(page.getByRole('button', { name: 'MCP 集成' })).toHaveCount(0)
  expect(mcpRequests).toBe(0)
})
