import { test, expect } from '@playwright/test'

const task = {
  id: 'task-reflection', title: '客户额度查询', question: '统计客户额度', datasource_id: 'sqlite-demo', status: 'completed',
  created_at: '2026-11-05T00:00:00Z', updated_at: '2026-11-05T00:00:00Z', artifacts: [],
  steps: [
    {id: 1, sequence: 0, kind: 'reflection', status: 'completed', duration_ms: 4, created_at: '2026-11-05T00:00:01Z', output: {
      failure_type: 'aggregation_grain', reason_codes: ['AGGREGATE_FANOUT_RISK'], action: 'regenerate_sql', attempt: 1,
      summary: '检测到聚合粒度风险，已重新生成查询。', mode: 'enforced', source: 'deterministic'
    }},
    {id: 2, sequence: 1, kind: 'query', status: 'completed', output: {sql: 'SELECT customer_id, SUM(amount) FROM orders GROUP BY customer_id'}}
  ],
  events: [{id: 1, kind: 'reflection_recorded', created_at: '2026-11-05T00:00:01Z', payload: {failure_type: 'aggregation_grain', action: 'regenerate_sql', attempt: 1, summary: '检测到聚合粒度风险，已重新生成查询。'}}]
}

test('任务详情以可解释时间线展示反思且窄屏无溢出', async ({ page }) => {
  let authenticated = false
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/auth/refresh')) return authenticated ? route.fulfill({json: {access_token: 'ui-token'}}) : route.fulfill({status: 401, json: {detail: 'not logged in'}})
    if (path.endsWith('/auth/login')) { authenticated = true; return route.fulfill({json: {access_token: 'ui-token'}}) }
    if (path.endsWith('/auth/me')) return route.fulfill({json: {username: 'viewer', roles: ['viewer'], tenant_id: 'default', is_active: true}})
    if (path.endsWith('/data-sources')) return route.fulfill({json: [{id: 'sqlite-demo', name: 'SQLite Demo', backend: 'sqlite'}]})
    if (path.endsWith('/tasks') && request.method() === 'GET') return route.fulfill({json: [task]})
    if (path.endsWith('/tasks/task-reflection')) return route.fulfill({json: task})
    return route.fulfill({status: 404, json: {detail: 'not mocked'}})
  })

  await page.setViewportSize({width: 390, height: 844})
  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('viewer')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button', {name: '登录'}).click()
  await page.goto('/tasks/task-reflection')

  await expect(page.getByText('结果质量反思')).toBeVisible()
  await expect(page.getByText('检测到聚合粒度风险，已重新生成查询。')).toBeVisible()
  await expect(page.getByText('第 1 次')).toBeVisible()
  await expect(page.getByText('强制执行')).toBeVisible()
  await expect(page.getByText('确定性规则')).toBeVisible()
  await expect(page.getByText('AGGREGATE_FANOUT_RISK')).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)
  expect(overflow).toBe(false)
})

