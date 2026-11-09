import { test, expect } from '@playwright/test'

test('退出后旧账号的延迟任务响应不会写入新会话', async ({ page }) => {
  let loginCount = 0
  let taskCount = 0
  let releaseOldTask
  const oldTaskReady = new Promise(resolve => { releaseOldTask = resolve })

  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/auth/login')) {
      loginCount += 1
      return route.fulfill({json: {access_token: `session-${loginCount}`}})
    }
    if (path.endsWith('/auth/refresh')) return route.fulfill({status: 401, json: {detail: 'not logged in'}})
    if (path.endsWith('/auth/me')) {
      return route.fulfill({json: {username: loginCount === 1 ? 'old-user' : 'new-user', roles: ['analyst'], tenant_id: 'default', is_active: true}})
    }
    if (path.endsWith('/data-sources')) {
      return route.fulfill({json: [{id: 'sqlite-demo', name: 'SQLite 演示库', backend: 'sqlite', readonly: true}]})
    }
    if (path.endsWith('/tasks')) {
      taskCount += 1
      if (taskCount === 1) {
        await oldTaskReady
        return route.fulfill({json: [{id: 'old-task', title: '旧账号任务', status: 'completed'}]})
      }
      return route.fulfill({json: [{id: 'new-task', title: '新账号任务', status: 'completed'}]})
    }
    if (path.endsWith('/auth/logout')) return route.fulfill({json: {ok: true}})
    return route.fulfill({status: 404, json: {detail: 'not mocked'}})
  })

  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('old-user')
  await page.getByPlaceholder('密码').fill('local1234')
  await page.getByRole('button', {name: '登录'}).click()
  await expect(page.getByRole('heading', {name: '你的数据，直接问。'})).toBeVisible()
  await expect.poll(() => taskCount).toBe(1)

  await page.getByRole('button', {name: '退出账号'}).click()
  await expect(page.getByRole('heading', {name: 'Query Pilot'})).toBeVisible()
  await page.getByPlaceholder('用户名').fill('new-user')
  await page.getByPlaceholder('密码').fill('local1234')
  await page.getByRole('button', {name: '登录'}).click()
  await expect(page.getByRole('heading', {name: '你的数据，直接问。'})).toBeVisible()
  await expect.poll(() => taskCount).toBeGreaterThan(1)

  releaseOldTask()
  await page.getByRole('button', {name: '数据任务'}).click()
  await expect(page.getByRole('button', {name: '新账号任务', exact: true}).last()).toBeVisible()
  await expect(page.getByRole('button', {name: /旧账号任务/})).toHaveCount(0)
  await expect(page.getByRole('button', {name: '退出账号'})).toBeVisible()
})
