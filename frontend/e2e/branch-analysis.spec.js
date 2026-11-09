import { test, expect } from '@playwright/test'

test('用户可从历史轮次创建隔离分析分支并比较结果', async ({ page }) => {
  let authenticated = false
  let branchCreated = false
  const queryBodies = []
  const mainResult = {
    task_id: 'task-main', trace_id: 'trace-main', question: '查询区域销售额', follow_up: false,
    answer: '主任务返回 2 行', sql: 'SELECT region, SUM(amount) AS total FROM sales GROUP BY region',
    columns: ['region', 'total'], rows: [['华东', 1960], ['华南', 980]], row_count: 2, truncated: false,
  }
  const branchOrigin = {
    parent_task_id: 'task-main', root_task_id: 'task-main', source_trace_id: 'trace-main', label: '华东假设', depth: 1,
  }
  const branchResult = {
    task_id: 'task-branch', trace_id: 'trace-branch', question: '假设只看华东区域', follow_up: false,
    answer: '分支返回 1 行', sql: 'SELECT region, SUM(amount) AS total FROM sales WHERE region = :region GROUP BY region',
    columns: ['region', 'total'], rows: [['华东', 1960]], row_count: 1, truncated: false, branch_origin: branchOrigin,
  }
  const mainConversation = {
    task_id: 'task-main', title: '查询区域销售额', datasource_id: 'sqlite-demo', status: 'completed', branch_origin: null,
    turns: [{id: 'trace-main', trace_id: 'trace-main', question: mainResult.question, follow_up: false, status: 'completed', result: mainResult}],
  }
  const branchConversation = {
    task_id: 'task-branch', title: '华东假设', datasource_id: 'sqlite-demo', status: 'completed', branch_origin: branchOrigin,
    turns: [{id: 'trace-branch', trace_id: 'trace-branch', question: branchResult.question, follow_up: false, status: 'completed', result: branchResult}],
  }

  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/auth/login')) { authenticated = true; return route.fulfill({json: {access_token: 'branch-token'}}) }
    if (path.endsWith('/auth/refresh')) return authenticated ? route.fulfill({json: {access_token: 'branch-token'}}) : route.fulfill({status: 401, json: {detail: 'not logged in'}})
    if (path.endsWith('/auth/me')) return route.fulfill({json: {username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true}})
    if (path.endsWith('/data-sources')) return route.fulfill({json: [{id: 'sqlite-demo', name: 'SQLite 演示库', backend: 'sqlite'}]})
    if (path.endsWith('/tasks/task-main/conversation')) return route.fulfill({json: mainConversation})
    if (path.endsWith('/tasks/task-branch/conversation')) return route.fulfill({json: branchConversation})
    if (path.endsWith('/tasks/task-main/branches') || path.endsWith('/tasks/task-branch/branches')) {
      const items = [{task_id: 'task-main', kind: 'main', label: '查询区域销售额', status: 'completed', answer: '主任务返回 2 行', row_count: 2, depth: 0}]
      if (branchCreated) items.push({task_id: 'task-branch', kind: 'branch', parent_task_id: 'task-main', root_task_id: 'task-main', source_trace_id: 'trace-main', label: '华东假设', status: 'completed', answer: '分支返回 1 行', row_count: 1, depth: 1})
      return route.fulfill({json: {root_task_id: 'task-main', items}})
    }
    if (path.endsWith('/tasks') && request.method() === 'GET') return route.fulfill({json: []})
    if (path.endsWith('/query') && request.method() === 'POST') {
      const body = request.postDataJSON()
      queryBodies.push(body)
      branchCreated = true
      return route.fulfill({json: branchResult})
    }
    return route.fulfill({status: 404, json: {detail: 'not mocked'}})
  })

  await page.goto('/workspace?task=task-main')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button', {name: '登录'}).click()

  await expect(page.getByRole('heading', {name: '主任务返回 2 行'})).toBeVisible()
  await page.getByRole('button', {name: '从此轮创建分析分支'}).click()
  await expect(page.getByText('来源轮次：查询区域销售额')).toBeVisible()
  await page.getByPlaceholder('输入分支假设…').fill('假设只看华东区域')
  await page.getByRole('button', {name: '创建分析分支', exact: true}).click()

  await expect.poll(() => queryBodies[0]).toMatchObject({
    question: '假设只看华东区域',
    branch_from_task_id: 'task-main',
    branch_from_trace_id: 'trace-main',
  })
  expect(queryBodies[0].task_id).toBeUndefined()
  expect(queryBodies[0].follow_up).toBeUndefined()
  await expect(page).toHaveURL(/\/workspace\?task=task-branch$/)
  await expect(page.getByText('分析分支 · 华东假设')).toBeVisible()
  await expect(page.getByRole('heading', {name: '分支返回 1 行'})).toBeVisible()
  await expect(page.getByRole('heading', {name: '假设对比'})).toBeVisible()
  await expect(page.locator('.branch-family-item')).toHaveCount(2)
  await page.setViewportSize({width: 390, height: 844})
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)

  await page.getByRole('button', {name: '返回来源任务'}).click()
  await expect(page).toHaveURL(/\/workspace\?task=task-main$/)
  await expect(page.getByRole('heading', {name: '主任务返回 2 行'})).toBeVisible()
})
