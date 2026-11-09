import { test, expect } from '@playwright/test'

function result({question, answer, traceId, followUp, rows}) {
  return {
    task_id: 'task-chat', trace_id: traceId, question, follow_up: followUp,
    answer, sql: followUp ? 'SELECT region, SUM(amount) FROM sales GROUP BY region' : 'SELECT SUM(amount) FROM sales',
    columns: followUp ? ['region', 'amount'] : ['amount'], rows, row_count: rows.length, truncated: false,
  }
}

test('工作台在同一对话框连续追问并可刷新恢复', async ({ page }) => {
  let authenticated = false
  const bodies = []
  const first = result({question: '查询销售总额', answer: '销售总额为 100', traceId: 'trace-1', followUp: false, rows: [[100]]})
  const second = result({question: '按区域拆分', answer: '华东区域最高', traceId: 'trace-2', followUp: true, rows: [['华东', 60], ['华南', 40]]})
  const projection = {
    task_id: 'task-chat', title: '查询销售总额', datasource_id: 'sqlite-demo', status: 'completed',
    turns: [
      {id: 'trace-1', question: first.question, follow_up: false, status: 'completed', result: first},
      {id: 'trace-2', question: second.question, follow_up: true, status: 'completed', result: second},
    ],
  }

  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/auth/login')) { authenticated = true; return route.fulfill({json: {access_token: 'chat-token'}}) }
    if (path.endsWith('/auth/refresh')) return authenticated ? route.fulfill({json: {access_token: 'chat-token'}}) : route.fulfill({status: 401, json: {detail: 'not logged in'}})
    if (path.endsWith('/auth/me')) return route.fulfill({json: {username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true}})
    if (path.endsWith('/data-sources')) return route.fulfill({json: [{id: 'sqlite-demo', name: 'SQLite 演示库', backend: 'sqlite'}]})
    if (path.endsWith('/tasks/task-chat/conversation')) return route.fulfill({json: projection})
    if (path.endsWith('/tasks') && request.method() === 'GET') return route.fulfill({json: [{id: 'task-chat', title: '查询销售总额', question: '查询销售总额', datasource_id: 'sqlite-demo', status: 'completed'}]})
    if (path.endsWith('/query') && request.method() === 'POST') {
      const body = request.postDataJSON()
      bodies.push(body)
      return route.fulfill({json: bodies.length === 1 ? first : second})
    }
    return route.fulfill({status: 404, json: {detail: 'not mocked'}})
  })

  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button', {name: '登录'}).click()

  await page.getByPlaceholder(/查询各区域销售额/).fill('查询销售总额')
  await page.getByRole('button', {name: '执行查询'}).click()
  await expect(page.getByRole('heading', {name: '销售总额为 100'})).toBeVisible()
  await expect(page).toHaveURL(/\/workspace\?task=task-chat$/)
  await expect(page.getByRole('combobox', {name: '选择数据源'})).toBeDisabled()

  await page.getByPlaceholder('继续提问…').fill('按区域拆分')
  await page.getByRole('button', {name: '发送追问'}).click()
  await expect(page.getByRole('heading', {name: '华东区域最高'})).toBeVisible()
  await expect.poll(() => bodies[1]).toMatchObject({task_id: 'task-chat', follow_up: true, question: '按区域拆分'})
  await expect(page.locator('.user-message')).toHaveText(['查询销售总额', '按区域拆分'])
  await expect(page.locator('.conversation-turn .result')).toHaveCount(2)
  const alignment = await page.locator('.conversation-turn').first().evaluate((turn) => {
    const user = turn.querySelector('.user-message').getBoundingClientRect()
    const assistant = turn.querySelector('.assistant-message').getBoundingClientRect()
    return {userRight: user.right, assistantLeft: assistant.left, viewportWidth: window.innerWidth}
  })
  expect(alignment.userRight).toBeGreaterThan(alignment.viewportWidth / 2)
  expect(alignment.assistantLeft).toBeLessThan(alignment.viewportWidth / 2)

  await page.reload()
  await expect(page.locator('.user-message')).toHaveText(['查询销售总额', '按区域拆分'])
  await expect(page.getByRole('heading', {name: '销售总额为 100'})).toBeVisible()
  await expect(page.getByRole('heading', {name: '华东区域最高'})).toBeVisible()

  await page.setViewportSize({width: 390, height: 844})
  await expect(page.getByPlaceholder('继续提问…')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
