import { test, expect } from '@playwright/test'

const task = {
  id: 'task-analysis', title: '月度销售洞察', question: '分析月度销售趋势和异常', datasource_id: 'sqlite-demo', status: 'completed',
  created_at: '2026-09-22T00:00:00Z', updated_at: '2026-09-22T00:00:00Z', events: [], artifacts: [],
  steps: [
    {id: 1, sequence: 0, kind: 'query', status: 'completed', output: {sql: 'SELECT month, amount FROM sales'}},
    {id: 2, sequence: 1, kind: 'analyze_result', status: 'completed', output: {
      mode: 'auto_insights', time_column: 'month', summary: '销售额整体上升，并识别到 1 个候选异常点。',
      trends: [{column: 'amount', start_period: '2025-01', end_period: '2026-01', points: 13, direction: 'up', percent_change: 20}],
      comparisons: [{kind: 'year_over_year', column: 'amount', baseline_period: '2025-01', current_period: '2026-01', absolute_change: 20, percent_change: 20}],
      anomalies: [{column: 'amount', period: '2025-08', value: 400, baseline: 100, score: 6, method: 'mad'}],
      limitations: ['查询结果已截断，洞察仅基于当前返回行。']
    }}
  ]
}

test('任务详情展示结构化洞察和数据限制', async ({ page }) => {
  let authenticated = false
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/auth/refresh')) return authenticated ? route.fulfill({json: {access_token: 'ui-token'}}) : route.fulfill({status: 401, json: {detail: 'not logged in'}})
    if (path.endsWith('/auth/login')) { authenticated = true; return route.fulfill({json: {access_token: 'ui-token'}}) }
    if (path.endsWith('/auth/me')) return route.fulfill({json: {username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true}})
    if (path.endsWith('/data-sources')) return route.fulfill({json: [{id: 'sqlite-demo', name: 'SQLite Demo', backend: 'sqlite'}]})
    if (path.endsWith('/tasks') && request.method() === 'GET') return route.fulfill({json: [task]})
    if (path.endsWith('/tasks/task-analysis')) return route.fulfill({json: task})
    return route.fulfill({status: 404, json: {detail: 'not mocked'}})
  })

  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button', {name: '登录'}).click()
  await page.getByRole('button', {name: '数据任务'}).click()
  await page.locator('.task-row').getByText('月度销售洞察', {exact: true}).click()

  await expect(page.getByRole('heading', {name: '结果洞察'})).toBeVisible()
  await expect(page.getByText('综合洞察')).toBeVisible()
  await expect(page.getByText('amount · 同比')).toBeVisible()
  await expect(page.getByText('amount · 2025-08')).toBeVisible()
  await expect(page.getByText('查询结果已截断，洞察仅基于当前返回行。')).toBeVisible()
})
