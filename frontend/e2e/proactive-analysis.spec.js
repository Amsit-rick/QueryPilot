import {test, expect} from '@playwright/test'

test('用户可显式创建启用暂停恢复并关闭主动分析计划', async ({page}) => {
  let authenticated=false
  let plan=null
  const createBodies=[]
  const basePlan={
    id:'plan-1',tenant_id:'default',owner_id:'1',state:'draft',next_run_at:null,last_run_at:null,last_task_id:'',
    runs_today:0,cost_today_usd:0,budget_date:null,created_at:'2026-11-08T09:00:00+00:00',updated_at:'2026-11-08T09:00:00+00:00',
    spec:{question:'每小时检查区域销售异常',datasource_id:'sqlite-demo',interval_minutes:60,max_runs_per_day:6,daily_budget_usd:0.5,require_approval:true},
  }
  await page.route('**/api/v1/**',async route=>{
    const request=route.request();const path=new URL(request.url()).pathname
    if(path.endsWith('/auth/login')){authenticated=true;return route.fulfill({json:{access_token:'proactive-token'}})}
    if(path.endsWith('/auth/refresh'))return authenticated?route.fulfill({json:{access_token:'proactive-token'}}):route.fulfill({status:401,json:{detail:'not logged in'}})
    if(path.endsWith('/auth/me'))return route.fulfill({json:{id:1,username:'analyst',roles:['analyst'],tenant_id:'default',is_active:true}})
    if(path.endsWith('/data-sources'))return route.fulfill({json:[{id:'sqlite-demo',name:'SQLite 演示库',backend:'sqlite'}]})
    if(path.endsWith('/tasks')&&request.method()==='GET')return route.fulfill({json:[]})
    if(path.endsWith('/proactive-analysis/status'))return route.fulfill({json:{enabled:true,min_interval_minutes:15,max_runs_per_day:24,max_daily_budget_usd:2,owner_id:'1'}})
    if(path.endsWith('/proactive-analysis/plans')&&request.method()==='GET')return route.fulfill({json:plan?[plan]:[]})
    if(path.endsWith('/proactive-analysis/plans')&&request.method()==='POST'){
      createBodies.push(request.postDataJSON());plan={...basePlan,spec:{...request.postDataJSON()}};return route.fulfill({json:plan})
    }
    if(path.endsWith('/proactive-analysis/plans/plan-1/runs'))return route.fulfill({json:[]})
    for(const action of ['activate','pause','resume','close']){
      if(path.endsWith(`/proactive-analysis/plans/plan-1/${action}`)){
        const state={activate:'active',pause:'paused',resume:'active',close:'closed'}[action]
        plan={...plan,state,next_run_at:state==='active'?'2026-11-08T10:00:00+00:00':null}
        return route.fulfill({json:plan})
      }
    }
    return route.fulfill({status:404,json:{detail:'not mocked'}})
  })

  await page.goto('/proactive-analysis')
  await page.getByPlaceholder('用户名').fill('analyst')
  await page.getByPlaceholder('密码').fill('route-password')
  await page.getByRole('button',{name:'登录'}).click()

  await expect(page.getByRole('heading',{name:'主动分析计划'})).toBeVisible()
  await page.getByLabel('分析问题').fill('每小时检查区域销售异常')
  await page.getByLabel('数据源').selectOption('sqlite-demo')
  await page.getByLabel('执行间隔（分钟）').fill('60')
  await page.getByLabel('每日最多执行').fill('6')
  await page.getByLabel('每日预算（美元）').fill('0.5')
  await page.getByRole('button',{name:'创建计划'}).click()

  await expect.poll(()=>createBodies[0]).toMatchObject({
    question:'每小时检查区域销售异常',datasource_id:'sqlite-demo',interval_minutes:60,max_runs_per_day:6,daily_budget_usd:0.5,require_approval:true,
  })
  await expect(page.locator('.proactive-state')).toHaveText('草稿')
  await page.getByRole('button',{name:'启用'}).click()
  await expect(page.locator('.proactive-state')).toHaveText('运行中')
  await page.getByRole('button',{name:'暂停'}).click()
  await expect(page.locator('.proactive-state')).toHaveText('已暂停')
  await page.getByRole('button',{name:'恢复'}).click()
  await expect(page.locator('.proactive-state')).toHaveText('运行中')
  await page.getByRole('button',{name:'关闭'}).click()
  await expect(page.locator('.proactive-state')).toHaveText('已关闭')
  await page.setViewportSize({width:390,height:844})
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth)).toBe(true)
})
