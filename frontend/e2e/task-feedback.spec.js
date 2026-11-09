import { test, expect } from '@playwright/test'

function taskDetail(events = []) {
  return {
    id: 'task-feedback', title: '区域销售结果', question: '查询区域销售额', datasource_id: 'sqlite-demo', status: 'completed',
    created_at: '2026-09-22T00:00:00Z', updated_at: '2026-09-22T00:00:00Z', events,
    steps: [{id: 1, sequence: 0, kind: 'query', status: 'completed', output: {sql: 'SELECT region, amount FROM sales'}}],
    artifacts: [{id: 'asset-1', kind: 'table', name: '查询结果', metadata: {columns: ['region', 'amount'], rows: [['华东', 100]], row_count: 1, preview_count: 1}}]
  }
}

test('任务详情可提交修正反馈并恢复反馈记录', async ({ page }) => {
  let current = taskDetail()
  let feedbackBody = null
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/auth/refresh')) return route.fulfill({status: 401, json: {detail: 'not logged in'}})
    if (path.endsWith('/auth/login')) return route.fulfill({json: {access_token: 'ui-token'}})
    if (path.endsWith('/auth/me')) return route.fulfill({json: {username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true}})
    if (path.endsWith('/data-sources')) return route.fulfill({json: [{id: 'sqlite-demo', name: 'SQLite Demo', backend: 'sqlite'}]})
    if (path.endsWith('/tasks') && request.method() === 'GET') return route.fulfill({json: [current]})
    if (path.endsWith('/tasks/task-feedback/feedback') && request.method() === 'POST') {
      feedbackBody = JSON.parse(request.postData())
      current = taskDetail([{id: 9, kind: 'feedback_recorded', payload: {outcome: 'corrected'}, created_at: '2026-09-22T00:02:00Z'}])
      return route.fulfill({json: {feedback_id: 'feedback-1', outcome: 'corrected'}})
    }
    if (path.endsWith('/tasks/task-feedback') && request.method() === 'GET') return route.fulfill({json: current})
    return route.fulfill({status: 404, json: {detail: 'not mocked'}})
  })

  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button', {name: '登录'}).click()
  await page.getByRole('button', {name: '数据任务'}).click()
  await page.locator('.task-row').getByText('区域销售结果', {exact: true}).click()

  await page.getByRole('button', {name: '修正'}).click()
  await page.getByPlaceholder('修正后的只读 SQL').fill('SELECT region, SUM(amount) FROM sales GROUP BY region')
  await page.getByPlaceholder('补充说明（可选）').fill('应按区域汇总')
  await page.getByRole('button', {name: '记录反馈'}).click()

  await expect.poll(() => feedbackBody).toEqual({
    outcome: 'corrected',
    note: '应按区域汇总',
    corrected_sql: 'SELECT region, SUM(amount) FROM sales GROUP BY region'
  })
  await expect(page.getByText('已记录 1 次')).toBeVisible()
  await expect(page.getByText('已修正')).toBeVisible()
})
