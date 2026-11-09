import { test, expect } from '@playwright/test'

function detail(artifacts = 1) {
  return {
    id: 'task-1', title: '查询销售额', question: '查询销售额', datasource_id: 'sqlite-demo',
    status: 'completed', created_at: '2026-09-20T00:00:00Z', updated_at: '2026-09-20T00:00:00Z',
    steps: [{id: 1, sequence: 0, kind: 'understand_question', status: 'completed', input: {question: '查询销售额'}, output: {}},
      {id: 2, sequence: 1, kind: 'query', status: 'completed', input: {}, output: {sql: 'SELECT region FROM sales', columns: ['region'], row_count: 2}}],
    events: [],
    artifacts: Array.from({length: artifacts}, (_, index) => ({id: `asset-${index}`, kind: 'table', name: '查询结果', metadata: {columns: ['region'], rows: [[index ? '华南' : '华东']], row_count: 1, preview_count: 1}}))
  }
}

test('已完成任务可以从详情页继续追问并刷新结果资产', async ({ page }) => {
  let currentDetail = detail()
  let followUpBody = null
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/auth/refresh')) return route.fulfill({status: 401, json: {detail: 'not logged in'}})
    if (path.endsWith('/auth/login')) return route.fulfill({json: {access_token: 'ui-token'}})
    if (path.endsWith('/auth/me')) return route.fulfill({json: {username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true}})
    if (path.endsWith('/data-sources')) return route.fulfill({json: [{id: 'sqlite-demo', name: 'SQLite Demo', backend: 'sqlite'}]})
    if (path.endsWith('/tasks') && request.method() === 'GET') return route.fulfill({json: [{id: 'task-1', title: '查询销售额', question: '查询销售额', datasource_id: 'sqlite-demo', status: 'completed'}]})
    if (path.endsWith('/tasks/task-1') && request.method() === 'GET') return route.fulfill({json: currentDetail})
    if (path === '/api/v1/query' && request.method() === 'POST') {
      followUpBody = JSON.parse(request.postData())
      currentDetail = detail(2)
      currentDetail.updated_at = '2026-09-20T00:01:00Z'
      return route.fulfill({json: {task_id: 'task-1', follow_up: true, sql: 'SELECT region FROM sales'}})
    }
    return route.fulfill({status: 404, json: {detail: 'not mocked'}})
  })

  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button', {name: '登录'}).click()
  await page.getByRole('button', {name: '数据任务'}).click()
  await page.locator('.task-row').getByText('查询销售额', {exact: true}).click()
  await expect(page.getByRole('heading', {name: '继续追问'})).toBeVisible()
  await page.getByPlaceholder(/基于当前任务继续提问/).fill('按区域继续拆分')
  await page.getByRole('button', {name: '提交追问'}).click()
  await expect.poll(() => followUpBody).toMatchObject({task_id: 'task-1', follow_up: true, question: '按区域继续拆分'})
  // DUR-01 idempotency: the workbench now sends a client_request_id with every
  // query so a retried follow-up cannot be executed twice.
  expect(followUpBody.client_request_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/)
  await expect(page.getByText('结果资产').locator('..').getByText('2')).toBeVisible()
})
