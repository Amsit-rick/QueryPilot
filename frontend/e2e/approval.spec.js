import { test, expect } from '@playwright/test'

test('查询可提交审批且不会渲染为空结果', async ({ page }) => {
  await page.route('**/api/v1/**', async route => {
    const url = route.request().url()
    if (url.endsWith('/health')) return route.fulfill({ json: { status: 'ok' } })
    if (url.endsWith('/api/v1/auth/login')) return route.fulfill({ json: { access_token: 'test-token' } })
    if (url.endsWith('/api/v1/auth/me')) return route.fulfill({ json: { username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true } })
    if (url.endsWith('/api/v1/data-sources')) return route.fulfill({ json: [{ id: 'sqlite-demo', name: 'SQLite Demo', backend: 'sqlite' }] })
    if (url.endsWith('/api/v1/sessions')) return route.fulfill({ json: [] })
    if (url.endsWith('/api/v1/query')) {
      const body = route.request().postDataJSON()
      expect(body.require_approval).toBe(true)
      return route.fulfill({ json: { status: 'approval_required', approval_id: 'approval-1', sql: 'SELECT region FROM sales', message: '高风险查询需要人工确认' } })
    }
    return route.fulfill({ status: 404, json: { detail: 'not mocked' } })
  })

  await page.goto('/')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill(process.env.TEST_PASSWORD || 'QueryPilot@20040508')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('heading', { name: '你的数据，直接问。' })).toBeVisible()
  await page.getByPlaceholder(/查询各区域销售额/).fill('查询各区域销售额')
  await page.getByLabel('提交审批').check()
  await page.getByRole('button', { name: /提交审批/ }).click()
  await expect(page.getByText('查询已提交审批')).toBeVisible()
  await expect(page.locator('.result')).toHaveCount(0)
})
