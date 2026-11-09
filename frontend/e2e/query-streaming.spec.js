import { test, expect } from '@playwright/test'

test('工作台消费版本化 NDJSON 查询结果', async ({ page }) => {
  let authenticated = false
  let streamRequests = 0
  const result = {
    task_id: 'task-stream', trace_id: 'trace-stream', question: '按区域查询', follow_up: false,
    answer: '查询完成，共返回 2 行。', sql: 'SELECT region, amount FROM sales',
    columns: ['region', 'amount'], row_count: 2, truncated: false, analysis: null,
  }
  const stream = [
    {version: '1', event: 'started', data: {stream_id: 'stream-1'}},
    {version: '1', event: 'result', data: result},
    {version: '1', event: 'rows', data: {offset: 0, rows: [['华东', 60]]}},
    {version: '1', event: 'rows', data: {offset: 1, rows: [['华南', 40]]}},
    {version: '1', event: 'completed', data: {row_count: 2, chunk_count: 2, truncated: false}},
  ].map(item => JSON.stringify(item)).join('\n') + '\n'

  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/auth/login')) { authenticated = true; return route.fulfill({json: {access_token: 'stream-token'}}) }
    if (path.endsWith('/auth/refresh')) return authenticated ? route.fulfill({json: {access_token: 'stream-token'}}) : route.fulfill({status: 401, json: {detail: 'not logged in'}})
    if (path.endsWith('/auth/me')) return route.fulfill({json: {username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true}})
    if (path.endsWith('/data-sources')) return route.fulfill({json: [{id: 'sqlite-demo', name: 'SQLite 演示库', backend: 'sqlite'}]})
    if (path.endsWith('/tasks') && request.method() === 'GET') return route.fulfill({json: []})
    if (path.endsWith('/query/stream') && request.method() === 'POST') {
      streamRequests += 1
      return route.fulfill({status: 200, contentType: 'application/x-ndjson', body: stream})
    }
    return route.fulfill({status: 404, json: {detail: 'not mocked'}})
  })

  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button', {name: '登录'}).click()
  await page.getByPlaceholder(/查询各区域销售额/).fill('按区域查询')
  await page.getByRole('button', {name: '执行查询'}).click()

  await expect(page.getByRole('heading', {name: '查询完成，共返回 2 行。'})).toBeVisible()
  await expect(page.getByRole('table').getByText('华东', {exact: true})).toBeVisible()
  await expect(page.getByRole('table').getByText('华南', {exact: true})).toBeVisible()
  expect(streamRequests).toBe(1)
})

test('工作台消费 v2 快照与有序增量', async ({ page }) => {
  let authenticated = false
  let streamRequests = 0
  const metadata = {
    task_id: 'task-v2', trace_id: 'trace-v2', question: '按区域查询', follow_up: false,
    answer: '查询完成，共返回 2 行。', sql: 'SELECT region, amount FROM sales',
    columns: ['region', 'amount'], row_count: 2, truncated: false, analysis: null,
  }
  const stream = [
    {version: '2', stream_id: 'stream-v2', sequence: 0, event: 'snapshot', data: {reason: 'initial', state: {status: 'running', terminal: false, result: null, rows: [], last_sequence: 0}}},
    {version: '2', stream_id: 'stream-v2', sequence: 1, event: 'started', data: {stream_id: 'stream-v2'}},
    {version: '2', stream_id: 'stream-v2', sequence: 2, event: 'result', data: metadata},
    {version: '2', stream_id: 'stream-v2', sequence: 3, event: 'rows', data: {offset: 0, rows: [['华东', 60], ['华南', 40]]}},
    {version: '2', stream_id: 'stream-v2', sequence: 4, event: 'completed', data: {row_count: 2, chunk_count: 1, truncated: false}},
  ].map(item => JSON.stringify(item)).join('\n') + '\n'

  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/auth/login')) { authenticated = true; return route.fulfill({json: {access_token: 'stream-token'}}) }
    if (path.endsWith('/auth/refresh')) return authenticated ? route.fulfill({json: {access_token: 'stream-token'}}) : route.fulfill({status: 401, json: {detail: 'not logged in'}})
    if (path.endsWith('/auth/me')) return route.fulfill({json: {username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true}})
    if (path.endsWith('/data-sources')) return route.fulfill({json: [{id: 'sqlite-demo', name: 'SQLite 演示库', backend: 'sqlite'}]})
    if (path.endsWith('/tasks') && request.method() === 'GET') return route.fulfill({json: []})
    if (path.endsWith('/query/stream') && request.method() === 'POST') {
      streamRequests += 1
      expect(request.headers()['x-query-stream-version']).toBe('2')
      return route.fulfill({status: 200, contentType: 'application/x-ndjson', body: stream})
    }
    return route.fulfill({status: 404, json: {detail: 'not mocked'}})
  })

  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button', {name: '登录'}).click()
  await page.getByPlaceholder(/查询各区域销售额/).fill('按区域查询')
  await page.getByRole('button', {name: '执行查询'}).click()

  await expect(page.getByRole('heading', {name: '查询完成，共返回 2 行。'})).toBeVisible()
  await expect(page.getByRole('table').getByText('华东', {exact: true})).toBeVisible()
  await expect(page.getByRole('table').getByText('华南', {exact: true})).toBeVisible()
  expect(streamRequests).toBe(1)
})

test('v2 流中断后按序恢复且不重复执行同步查询', async ({ page }) => {
  let authenticated = false
  let streamRequests = 0
  let syncRequests = 0
  let resumedAfter = null
  const metadata = {
    task_id: 'task-resume', trace_id: 'trace-resume', question: '恢复查询', follow_up: false,
    answer: '查询完成，共返回 2 行。', sql: 'SELECT region, amount FROM sales',
    columns: ['region', 'amount'], row_count: 2, truncated: false, analysis: null,
  }
  const first = [
    {version: '2', stream_id: 'stream-resume', sequence: 0, event: 'snapshot', data: {reason: 'initial', state: {status: 'running', terminal: false, result: null, rows: [], last_sequence: 0}}},
    {version: '2', stream_id: 'stream-resume', sequence: 1, event: 'started', data: {stream_id: 'stream-resume'}},
    {version: '2', stream_id: 'stream-resume', sequence: 2, event: 'result', data: metadata},
    {version: '2', stream_id: 'stream-resume', sequence: 3, event: 'rows', data: {offset: 0, rows: [['华东', 60]]}},
  ].map(item => JSON.stringify(item)).join('\n') + '\n'
  const resumed = [
    {version: '2', stream_id: 'stream-resume', sequence: 4, event: 'rows', data: {offset: 1, rows: [['华南', 40]]}},
    {version: '2', stream_id: 'stream-resume', sequence: 5, event: 'completed', data: {row_count: 2, chunk_count: 2, truncated: false}},
  ].map(item => JSON.stringify(item)).join('\n') + '\n'

  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    if (path.endsWith('/auth/login')) { authenticated = true; return route.fulfill({json: {access_token: 'stream-token'}}) }
    if (path.endsWith('/auth/refresh')) return authenticated ? route.fulfill({json: {access_token: 'stream-token'}}) : route.fulfill({status: 401, json: {detail: 'not logged in'}})
    if (path.endsWith('/auth/me')) return route.fulfill({json: {username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true}})
    if (path.endsWith('/data-sources')) return route.fulfill({json: [{id: 'sqlite-demo', name: 'SQLite 演示库', backend: 'sqlite'}]})
    if (path.endsWith('/tasks') && request.method() === 'GET') return route.fulfill({json: []})
    if (path.endsWith('/query/stream') && request.method() === 'POST') {
      streamRequests += 1
      expect(request.headers()['x-query-stream-version']).toBe('2')
      if (streamRequests === 1) return route.fulfill({status: 200, contentType: 'application/x-ndjson', body: first})
      resumedAfter = url.searchParams.get('after_sequence')
      return route.fulfill({status: 200, contentType: 'application/x-ndjson', body: resumed})
    }
    if (path.endsWith('/query') && request.method() === 'POST') {
      syncRequests += 1
      return route.fulfill({status: 500, json: {detail: 'must not replay'}})
    }
    return route.fulfill({status: 404, json: {detail: 'not mocked'}})
  })

  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button', {name: '登录'}).click()
  await page.getByPlaceholder(/查询各区域销售额/).fill('恢复查询')
  await page.getByRole('button', {name: '执行查询'}).click()

  await expect(page.getByRole('heading', {name: '查询完成，共返回 2 行。'})).toBeVisible()
  await expect(page.getByRole('table').getByText('华东', {exact: true})).toHaveCount(1)
  await expect(page.getByRole('table').getByText('华南', {exact: true})).toHaveCount(1)
  expect(streamRequests).toBe(2)
  expect(resumedAfter).toBe('3')
  expect(syncRequests).toBe(0)
})

test('v2 游标过期时用完整快照替换本地投影', async ({ page }) => {
  let authenticated = false
  let streamRequests = 0
  const metadata = {
    task_id: 'task-snapshot', trace_id: 'trace-snapshot', question: '快照恢复', follow_up: false,
    answer: '查询完成，共返回 2 行。', sql: 'SELECT region, amount FROM sales',
    columns: ['region', 'amount'], row_count: 2, truncated: false, analysis: null,
  }
  const first = [
    {version: '2', stream_id: 'stream-snapshot', sequence: 0, event: 'snapshot', data: {reason: 'initial', state: {status: 'running', terminal: false, result: null, rows: [], last_sequence: 0}}},
    {version: '2', stream_id: 'stream-snapshot', sequence: 1, event: 'started', data: {stream_id: 'stream-snapshot'}},
    {version: '2', stream_id: 'stream-snapshot', sequence: 2, event: 'result', data: metadata},
    {version: '2', stream_id: 'stream-snapshot', sequence: 3, event: 'rows', data: {offset: 0, rows: [['华东', 60]]}},
  ].map(item => JSON.stringify(item)).join('\n') + '\n'
  const recovered = JSON.stringify({
    version: '2', stream_id: 'stream-snapshot', sequence: 5, event: 'snapshot',
    data: {reason: 'cursor_expired', state: {status: 'completed', terminal: true, result: metadata, rows: [['华东', 60], ['华南', 40]], completion: {row_count: 2}, last_sequence: 5}},
  }) + '\n'

  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/auth/login')) { authenticated = true; return route.fulfill({json: {access_token: 'stream-token'}}) }
    if (path.endsWith('/auth/refresh')) return authenticated ? route.fulfill({json: {access_token: 'stream-token'}}) : route.fulfill({status: 401, json: {detail: 'not logged in'}})
    if (path.endsWith('/auth/me')) return route.fulfill({json: {username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true}})
    if (path.endsWith('/data-sources')) return route.fulfill({json: [{id: 'sqlite-demo', name: 'SQLite 演示库', backend: 'sqlite'}]})
    if (path.endsWith('/tasks') && request.method() === 'GET') return route.fulfill({json: []})
    if (path.endsWith('/query/stream') && request.method() === 'POST') {
      streamRequests += 1
      return route.fulfill({status: 200, contentType: 'application/x-ndjson', body: streamRequests === 1 ? first : recovered})
    }
    return route.fulfill({status: 404, json: {detail: 'not mocked'}})
  })

  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button', {name: '登录'}).click()
  await page.getByPlaceholder(/查询各区域销售额/).fill('快照恢复')
  await page.getByRole('button', {name: '执行查询'}).click()

  await expect(page.getByRole('heading', {name: '查询完成，共返回 2 行。'})).toBeVisible()
  await expect(page.getByRole('table').getByText('华东', {exact: true})).toHaveCount(1)
  await expect(page.getByRole('table').getByText('华南', {exact: true})).toHaveCount(1)
  expect(streamRequests).toBe(2)
})

test('v2 重复序号失败关闭且不重放同步查询', async ({ page }) => {
  let authenticated = false
  let syncRequests = 0
  const stream = [
    {version: '2', stream_id: 'stream-sequence', sequence: 0, event: 'snapshot', data: {reason: 'initial', state: {status: 'running', terminal: false, result: null, rows: [], last_sequence: 0}}},
    {version: '2', stream_id: 'stream-sequence', sequence: 1, event: 'started', data: {stream_id: 'stream-sequence'}},
    {version: '2', stream_id: 'stream-sequence', sequence: 1, event: 'progress', data: {phase: 'querying'}},
  ].map(item => JSON.stringify(item)).join('\n') + '\n'

  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/auth/login')) { authenticated = true; return route.fulfill({json: {access_token: 'stream-token'}}) }
    if (path.endsWith('/auth/refresh')) return authenticated ? route.fulfill({json: {access_token: 'stream-token'}}) : route.fulfill({status: 401, json: {detail: 'not logged in'}})
    if (path.endsWith('/auth/me')) return route.fulfill({json: {username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true}})
    if (path.endsWith('/data-sources')) return route.fulfill({json: [{id: 'sqlite-demo', name: 'SQLite 演示库', backend: 'sqlite'}]})
    if (path.endsWith('/tasks') && request.method() === 'GET') return route.fulfill({json: []})
    if (path.endsWith('/query/stream') && request.method() === 'POST') return route.fulfill({status: 200, contentType: 'application/x-ndjson', body: stream})
    if (path.endsWith('/query') && request.method() === 'POST') {
      syncRequests += 1
      return route.fulfill({status: 500, json: {detail: 'must not replay'}})
    }
    return route.fulfill({status: 404, json: {detail: 'not mocked'}})
  })

  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button', {name: '登录'}).click()
  await page.getByPlaceholder(/查询各区域销售额/).fill('重复序号')
  await page.getByRole('button', {name: '执行查询'}).click()

  await expect(page.getByText('查询流事件序号不连续')).toBeVisible()
  expect(syncRequests).toBe(0)
})

test('未知流协议版本失败关闭且不重放同步查询', async ({ page }) => {
  let authenticated = false
  let streamRequests = 0
  let syncRequests = 0
  const stream = JSON.stringify({version: '999', event: 'started', data: {stream_id: 'stream-bad-version'}}) + '\n'

  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/auth/login')) { authenticated = true; return route.fulfill({json: {access_token: 'stream-token'}}) }
    if (path.endsWith('/auth/refresh')) return authenticated ? route.fulfill({json: {access_token: 'stream-token'}}) : route.fulfill({status: 401, json: {detail: 'not logged in'}})
    if (path.endsWith('/auth/me')) return route.fulfill({json: {username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true}})
    if (path.endsWith('/data-sources')) return route.fulfill({json: [{id: 'sqlite-demo', name: 'SQLite 演示库', backend: 'sqlite'}]})
    if (path.endsWith('/tasks') && request.method() === 'GET') return route.fulfill({json: []})
    if (path.endsWith('/query/stream') && request.method() === 'POST') {
      streamRequests += 1
      return route.fulfill({status: 200, contentType: 'application/x-ndjson', body: stream})
    }
    if (path.endsWith('/query') && request.method() === 'POST') {
      syncRequests += 1
      return route.fulfill({status: 500, json: {detail: 'must not replay'}})
    }
    return route.fulfill({status: 404, json: {detail: 'not mocked'}})
  })

  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button', {name: '登录'}).click()
  await page.getByPlaceholder(/查询各区域销售额/).fill('测试未知协议')
  await page.getByRole('button', {name: '执行查询'}).click()

  await expect(page.getByText('流式响应协议不兼容')).toBeVisible()
  expect(streamRequests).toBe(1)
  expect(syncRequests).toBe(0)
})

test('超大流事件失败关闭且不重放同步查询', async ({ page }) => {
  let authenticated = false
  let streamRequests = 0
  let syncRequests = 0
  const oversizedEvent = '{"version":"1","event":"progress","data":{"padding":"' + 'x'.repeat(5 * 1024 * 1024) + '"}}'

  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/auth/login')) { authenticated = true; return route.fulfill({json: {access_token: 'stream-token'}}) }
    if (path.endsWith('/auth/refresh')) return authenticated ? route.fulfill({json: {access_token: 'stream-token'}}) : route.fulfill({status: 401, json: {detail: 'not logged in'}})
    if (path.endsWith('/auth/me')) return route.fulfill({json: {username: 'admin', roles: ['admin'], tenant_id: 'default', is_active: true}})
    if (path.endsWith('/data-sources')) return route.fulfill({json: [{id: 'sqlite-demo', name: 'SQLite 演示库', backend: 'sqlite'}]})
    if (path.endsWith('/tasks') && request.method() === 'GET') return route.fulfill({json: []})
    if (path.endsWith('/query/stream') && request.method() === 'POST') {
      streamRequests += 1
      return route.fulfill({status: 200, contentType: 'application/x-ndjson', body: oversizedEvent})
    }
    if (path.endsWith('/query') && request.method() === 'POST') {
      syncRequests += 1
      return route.fulfill({status: 500, json: {detail: 'must not replay'}})
    }
    return route.fulfill({status: 404, json: {detail: 'not mocked'}})
  })

  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button', {name: '登录'}).click()
  await page.getByPlaceholder(/查询各区域销售额/).fill('测试超大事件')
  await page.getByRole('button', {name: '执行查询'}).click()

  await expect(page.getByText('单个流式事件超过大小限制')).toBeVisible()
  expect(streamRequests).toBe(1)
  expect(syncRequests).toBe(0)
})
