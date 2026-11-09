import { test, expect } from '@playwright/test'

test('管理员可以创建企业语义条目', async ({ page }) => {
  const entries = []
  await page.route('**/api/v1/**', async route => {
    const url = route.request().url()
    if (url.endsWith('/api/v1/auth/login')) return route.fulfill({ json: { access_token: 'test-token' } })
    if (url.endsWith('/api/v1/auth/me')) return route.fulfill({ json: { username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true } })
    if (url.endsWith('/api/v1/data-sources')) return route.fulfill({ json: [{ id: 'sqlite-demo', name: 'SQLite Demo', backend: 'sqlite' }] })
    if (url.includes('/api/v1/tasks')) return route.fulfill({ json: [] })
    if (url.includes('/api/v1/semantics') && route.request().method() === 'GET') return route.fulfill({ json: entries })
    if (url.endsWith('/api/v1/semantics') && route.request().method() === 'POST') {
      const body = route.request().postDataJSON()
      entries.push({ id: 'semantic-1', logical_id: 'logical-1', version: 1, active: true, ...body })
      return route.fulfill({ json: entries[0] })
    }
    return route.fulfill({ status: 404, json: { detail: 'not mocked' } })
  })

  await page.goto('/')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('local-test-password')
  await page.getByRole('button', { name: '登录' }).click()
  await page.getByRole('button', { name: /语义层/ }).click()
  await expect(page.getByRole('heading', { name: '企业语义层' })).toBeVisible()

  await page.getByPlaceholder(/revenue/).fill('销售额')
  await page.getByPlaceholder('如 营收、销售收入').fill('营收, 销售收入')
  await page.getByPlaceholder('如 区域、汇总、趋势').fill('区域, 汇总')
  await page.getByPlaceholder('{}').fill('{"formula":"SUM(amount)","tables":["sales"],"columns":["amount"]}')
  await page.getByRole('button', { name: '新增条目' }).click()

  await expect.poll(() => entries[0]?.definition?.aliases).toEqual(['营收', '销售收入'])
  await expect.poll(() => entries[0]?.definition?.keywords).toEqual(['区域', '汇总'])
  await expect(page.locator('.semantic-entry').getByText('销售额')).toBeVisible()
  await expect(page.locator('.semantic-entry').getByText('指标')).toBeVisible()
})
