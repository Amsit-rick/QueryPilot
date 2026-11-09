import { test, expect } from '@playwright/test'

function taskDetail(spec, rows = [['华东', 1960], ['华南', 980], ['华北', 760]], columns = ['region', 'amount']) {
  const sourceKey = 'task-1:table:result'
  return {
    id: 'task-1', title: '区域销售额', question: '统计各区域销售额', datasource_id: 'mysql-demo', status: 'completed',
    created_at: '2026-09-20T00:00:00Z', updated_at: '2026-09-20T00:00:00Z', steps: [], events: [],
    artifacts: [
      {id: 'table-1', kind: 'table', name: '查询结果', metadata: {idempotency_key: sourceKey, columns, rows, row_count: rows.length, preview_count: rows.length}},
      {id: 'chart-1', kind: 'chart', name: '查询图表配置', metadata: {source_artifact_idempotency_key: sourceKey, spec}}
    ]
  }
}

async function mockApp(page, getDetail) {
  let authenticated = false
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/auth/refresh')) return authenticated
      ? route.fulfill({json: {access_token: 'ui-token'}})
      : route.fulfill({status: 401, json: {detail: 'not logged in'}})
    if (path.endsWith('/auth/login')) {
      authenticated = true
      return route.fulfill({json: {access_token: 'ui-token'}})
    }
    if (path.endsWith('/auth/me')) return route.fulfill({json: {username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true}})
    if (path.endsWith('/data-sources')) return route.fulfill({json: [{id: 'mysql-demo', name: 'MySQL Demo', backend: 'mysql'}]})
    if (path.endsWith('/tasks') && request.method() === 'GET') return route.fulfill({json: [{id: 'task-1', title: '区域销售额', question: '统计各区域销售额', datasource_id: 'mysql-demo', status: 'completed'}]})
    if (path.endsWith('/tasks/task-1')) return route.fulfill({json: getDetail()})
    return route.fulfill({status: 404, json: {detail: 'not mocked'}})
  })
}

async function loginAndOpenTask(page) {
  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button', {name: '登录'}).click()
  await page.getByRole('button', {name: '数据任务'}).click()
  await page.locator('.task-row').getByText('区域销售额', {exact: true}).click()
}

test('任务图表从资产渲染并在刷新后恢复', async ({ page }) => {
  const spec = {schema_version: '1.0', kind: 'bar', x_column: 'region', y_columns: ['amount'], reason: '分类与数值对比', column_types: ['categorical', 'numeric'], max_points: 100}
  await mockApp(page, () => taskDetail(spec))
  await loginAndOpenTask(page)

  await expect(page.getByTestId('safe-chart')).toBeVisible()
  await expect(page.locator('.asset-bar-axis')).toBeVisible()
  await expect(page.getByText('华东', {exact: true})).toBeVisible()
  await page.getByRole('button', {name: '表格'}).click()
  await expect(page.locator('.task-result table')).toBeVisible()
  await page.getByRole('button', {name: '图表'}).click()
  await page.reload()
  await expect(page.getByTestId('safe-chart')).toBeVisible()
  await expect(page.getByRole('button', {name: '图表'})).toHaveAttribute('aria-pressed', 'true')
})

test('任务详情拒绝带任意渲染字段的图表资产', async ({ page }) => {
  const unsafeSpec = {schema_version: '1.0', kind: 'bar', x_column: 'region', y_columns: ['amount'], max_points: 100, remote_url: 'https://example.invalid/chart.js'}
  await mockApp(page, () => taskDetail(unsafeSpec))
  await loginAndOpenTask(page)

  await expect(page.getByText('图表配置未通过安全校验，请使用表格查看结果。')).toBeVisible()
  await expect(page.getByTestId('safe-chart')).toHaveCount(0)
  await page.getByRole('button', {name: '表格'}).click()
  await expect(page.locator('.task-result table')).toBeVisible()
})

test('白名单折线图饼图和散点图均使用本地渲染器', async ({ page }) => {
  let kind = 'line'
  const spec = () => ({schema_version: '1.0', kind, x_column: 'region', y_columns: ['amount'], reason: '安全图表', column_types: [kind === 'scatter' ? 'numeric' : 'categorical', 'numeric'], max_points: 100})
  await mockApp(page, () => taskDetail(spec(), kind === 'scatter' ? [[1, 10], [2, 30], [3, 20]] : undefined))
  await loginAndOpenTask(page)

  await expect(page.locator('.plot-line')).toHaveCount(1)
  await expect(page.locator('.plot-tick-label').first()).toBeVisible()
  kind = 'pie'
  await page.reload()
  await expect(page.locator('.asset-pie')).toBeVisible()
  kind = 'scatter'
  await page.reload()
  await expect(page.locator('.asset-plot circle')).toHaveCount(3)
})

test('用户可以选择图形和统计字段并立即重绘当前结果', async ({ page }) => {
  const spec = {schema_version: '1.0', kind: 'bar', x_column: 'region', y_columns: ['amount'], reason: '安全图表', column_types: ['categorical', 'numeric', 'numeric'], max_points: 100}
  await mockApp(page, () => taskDetail(spec, [['华东', 700628735.07, 12], ['华南', 700510000.3, 8], ['华北', 700471551.3025, 5]], ['region', 'amount', 'units']))
  await loginAndOpenTask(page)

  await expect(page.getByTestId('chart-configurator')).toBeVisible()
  await page.getByTestId('chart-kind-select').selectOption('pie')
  await expect(page.locator('.asset-pie')).toBeVisible()
  await page.getByTestId('chart-kind-select').selectOption('column')
  await expect(page.locator('.column-bar')).toHaveCount(3)
  await expect(page.locator('.plot-tick-label').first()).toBeVisible()
  await expect(page.locator('.plot-tick-label').filter({hasText: '亿'}).first()).toBeVisible()
  await expect(page.getByTestId('chart-kind-select').locator('option[value="rose"]')).toHaveCount(0)
  await page.getByTestId('chart-kind-select').selectOption('line')
  await expect(page.locator('.plot-tick-label').filter({hasText: '亿'}).first()).toBeVisible()
  const yTickCount = await page.locator('.plot-grid').count()
  const yTickLabels = await page.locator('.plot-tick-label').evaluateAll(nodes => nodes.slice(0, 5).map(node => node.textContent))
  expect(new Set(yTickLabels.slice(0, yTickCount)).size).toBe(yTickCount)
  const hasClippedAxisText = await page.locator('.asset-plot svg text').evaluateAll(nodes => nodes.some(node => {
    const box = node.getBBox()
    return box.x < 0 || box.y < 0 || box.x + box.width > 960 || box.y + box.height > 430
  }))
  expect(hasClippedAxisText).toBe(false)
  await page.getByTestId('chart-y-select').selectOption('units')
  await expect(page.locator('.plot-line')).toBeVisible()
  await expect(page.locator('.plot-axis-label')).toHaveCount(2)

  await page.getByTestId('chart-kind-select').selectOption('scatter')
  await page.getByTestId('chart-x-select').selectOption('amount')
  await page.getByTestId('chart-y-select').selectOption('units')
  await expect(page.locator('.asset-plot circle')).toHaveCount(3)
  await expect(page.locator('.plot-tick-label').first()).toBeVisible()
  await expect(page.locator('.plot-axis-label')).toHaveCount(2)
})
