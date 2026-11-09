import {test,expect} from '@playwright/test'

test('管理员创建 Skill 版本并执行灰度、发布和历史回滚',async({page})=>{
  const builtin={id:'sales-analysis',version:'1.0.0',name:'销售分析',description:'内置销售分析',intents:['sales_query'],trigger_terms:['销售'],allowed_tools:['schema.list','query.plan','result.get'],workflow_steps:['确认口径','生成只读计划'],allowed_roles:['viewer','analyst','admin'],read_only:true,priority:70}
  let versions=[]
  let createdDefinition=null
  let deferQuality=false
  let releaseQuality=null
  await page.route('**/api/v1/**',async route=>{
    const request=route.request();const path=new URL(request.url()).pathname
    if(path.endsWith('/auth/refresh'))return route.fulfill({status:401,json:{detail:'not logged in'}})
    if(path.endsWith('/auth/login'))return route.fulfill({json:{access_token:'ui-token'}})
    if(path.endsWith('/auth/me'))return route.fulfill({json:{username:'admin',roles:['admin'],tenant_id:'tenant-a',is_active:true}})
    if(path.endsWith('/data-sources'))return route.fulfill({json:[{id:'sqlite-demo',name:'SQLite Demo',backend:'sqlite'}]})
    if(path.endsWith('/tasks'))return route.fulfill({json:[]})
    if(path.endsWith('/skills')&&request.method()==='GET')return route.fulfill({json:{version:2,read_only:true,skills:[builtin]}})
    if(path.endsWith('/skills/admin/catalog'))return route.fulfill({json:[builtin]})
    if(path.endsWith('/skills/admin/versions')&&request.method()==='GET')return route.fulfill({json:versions})
    if(path.endsWith('/skills/admin/evaluate'))return route.fulfill({json:{evaluation_type:'skill_routing_only',summary:{cases:9,route_changes:1,version_changes:1,baseline_matches:8,candidate_matches:9,baseline_match_rate:8/9,candidate_match_rate:1},cases:[{id:'sales-gross-margin',route_changed:true,baseline:{skill_id:null},candidate:{skill_id:'sales-analysis'}}]}})
    if(path.endsWith('/skills/admin/evaluate-query-quality')){
      if(deferQuality){deferQuality=false;await new Promise(resolve=>releaseQuality=resolve)}
      return route.fulfill({json:{evaluation_type:'skill_sql_result_quality',dataset_version:'2026-11-04-v3',database:'isolated_synthetic_sqlite_fixture',model:'mock-model',prompt_version:'prompt-v3',generation_attempts:22,summary:{cases:11,baseline:{generation_success_rate:1,sql_valid_rate:1,guardrail_pass_rate:1,execution_success_rate:1,result_exact_match_rate:1/11},candidate:{generation_success_rate:1,sql_valid_rate:1,guardrail_pass_rate:1,execution_success_rate:1,result_exact_match_rate:1},},cases:Array.from({length:11},(_,index)=>({id:`case-${index+1}`,category:index===10?'multi_fact_preaggregation':'relational_join',baseline:{status:index===10?'passed':'result_mismatch'},candidate:{status:'passed'}})),limitations:[]}})
    }
    if(path.endsWith('/skills/admin/versions')&&request.method()==='POST'){
      const body=JSON.parse(request.postData());createdDefinition=body.definition;const item={id:'skill-v2',tenant_id:'tenant-a',state:'draft',canary_percent:0,created_by:'admin',created_at:'2026-11-03T10:00:00Z',updated_at:'2026-11-03T10:00:00Z',definition:body.definition};versions=[item,...versions];return route.fulfill({json:item})
    }
    if(path.endsWith('/skills/admin/versions/skill-v2/canary')){versions=versions.map(item=>item.id==='skill-v2'?{...item,state:'canary',canary_percent:25}:item);return route.fulfill({json:versions[0]})}
    if(path.endsWith('/skills/admin/versions/skill-v2/publish')){versions=versions.map(item=>item.id==='skill-v2'?{...item,state:'active',canary_percent:0}:item);return route.fulfill({json:versions[0]})}
    if(path.endsWith('/skills/admin/versions/skill-v1/rollback')){versions=versions.map(item=>item.id==='skill-v1'?{...item,state:'active'}:{...item,state:'retired'});return route.fulfill({json:versions.find(item=>item.id==='skill-v1')})}
    if(path.endsWith('/skills/admin/preview'))return route.fulfill({json:{skill_id:'sales-analysis',subject:'viewer-7',bucket:18,canary_percent:25,canary_version:'1.1.0',active_version:'1.0.0',selected_version:'1.1.0',selected_state:'canary'}})
    return route.fulfill({status:404,json:{detail:'not mocked'}})
  })
  versions=[{id:'skill-v1',tenant_id:'tenant-a',state:'retired',canary_percent:0,created_by:'admin',created_at:'2026-11-02T10:00:00Z',updated_at:'2026-11-02T10:00:00Z',definition:{...builtin,version:'0.9.0'}}]
  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin');await page.getByPlaceholder('密码').fill('route-password');await page.getByRole('button',{name:'登录'}).click()
  await page.getByRole('button',{name:'Skill 发布'}).click()
  await page.getByLabel('目标 Skill').selectOption('sales-analysis')
  await page.getByLabel('新版本号').fill('1.1.0')
  await page.getByLabel('版本说明').fill('增强销售口径识别')
  await page.getByLabel('触发词').fill('销售\n营收\n毛利')
  await page.getByLabel('适用意图').fill('sales_query\ngross_margin_query')
  await page.getByLabel('工作步骤').fill('确认指标口径\n定位授权字段\n生成只读查询计划')
  await page.getByRole('button',{name:'对比路由效果'}).click()
  await expect(page.getByText('固定集路由评测')).toBeVisible()
  await expect(page.getByText('命中率 89% → 100%')).toBeVisible()
  await page.getByRole('button',{name:'评测 SQL 效果'}).click()
  await expect(page.getByText('SQL 与结果质量对照')).toBeVisible()
  await expect(page.getByText('SQL 与结果质量对照 · 11 条用例')).toBeVisible()
  await expect(page.getByText('结果匹配 9%')).toBeVisible()
  await expect(page.getByText('结果匹配 100%')).toBeVisible()
  await expect(page.getByText('模型 mock-model · Prompt prompt-v3 · 生成尝试 22 次')).toBeVisible()
  await expect(page.getByText('结果不匹配').first()).toBeVisible()
  for(const [width,height] of [[1440,900],[768,900],[390,844]]){
    await page.setViewportSize({width,height})
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true)
  }
  await page.setViewportSize({width:1440,height:900})
  deferQuality=true
  await page.getByRole('button',{name:'评测 SQL 效果'}).click()
  await expect.poll(()=>Boolean(releaseQuality)).toBe(true)
  await page.getByLabel('版本说明').fill('评测期间修改的候选说明')
  releaseQuality()
  await expect(page.getByText('评测期间草稿已变更，旧结果已丢弃，请重新评测')).toBeVisible()
  await page.getByRole('button',{name:'创建草稿'}).click()
  await expect(page.getByText('1.1.0')).toBeVisible()
  expect(createdDefinition.trigger_terms).toEqual(['销售','营收','毛利'])
  expect(createdDefinition.intents).toEqual(['sales_query','gross_margin_query'])
  expect(createdDefinition.workflow_steps).toEqual(['确认指标口径','定位授权字段','生成只读查询计划'])
  await page.getByLabel('灰度比例').fill('25')
  await page.getByRole('button',{name:'开始灰度'}).click()
  await expect(page.locator('.skill-state.canary')).toContainText(/灰度.*25%/)
  await page.getByLabel('预览用户').fill('viewer-7')
  await page.getByRole('button',{name:'预览命中'}).click()
  await expect(page.getByText('命中 1.1.0')).toBeVisible()
  await page.getByRole('button',{name:'正式发布'}).click()
  await expect(page.getByText('当前生效')).toBeVisible()
  await page.getByRole('button',{name:'回滚到此版本'}).click()
  await expect(page.getByText('0.9.0')).toBeVisible()
  for(const [width,height] of [[1440,900],[768,900],[390,844]]){
    await page.setViewportSize({width,height})
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true)
  }
})
