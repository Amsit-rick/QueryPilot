import {test,expect} from '@playwright/test'

test('管理员注册模型提供商并创建提示词版本且不提交明文密钥',async({page})=>{
  let providers=[];let prompts=[];let providerBody=null;let promptBody=null
  await page.route('**/api/v1/**',async route=>{
    const request=route.request();const path=new URL(request.url()).pathname
    if(path.endsWith('/auth/refresh'))return route.fulfill({status:401,json:{detail:'not logged in'}})
    if(path.endsWith('/auth/login'))return route.fulfill({json:{access_token:'ui-token'}})
    if(path.endsWith('/auth/me'))return route.fulfill({json:{username:'admin',roles:['admin'],tenant_id:'default',is_active:true}})
    if(path.endsWith('/data-sources'))return route.fulfill({json:[{id:'sqlite-demo',name:'SQLite Demo',backend:'sqlite'}]})
    if(path.endsWith('/tasks'))return route.fulfill({json:[]})
    if(path.endsWith('/model-ops/providers')&&request.method()==='GET')return route.fulfill({json:providers})
    if(path.endsWith('/model-ops/providers')&&request.method()==='POST'){
      providerBody=JSON.parse(request.postData());providers=[{id:'provider-1',...providerBody,is_default:true,health_status:'unchecked',credential_configured:false,last_checked_at:null,last_latency_ms:0}]
      return route.fulfill({json:providers[0]})
    }
    if(path.endsWith('/model-ops/providers/provider-1/health')){
      providers=[{...providers[0],health_status:'healthy',credential_configured:true,last_checked_at:'2026-09-20T10:00:00Z',last_latency_ms:42}]
      return route.fulfill({json:providers[0]})
    }
    if(path.endsWith('/model-ops/prompts')&&request.method()==='GET')return route.fulfill({json:prompts})
    if(path.endsWith('/model-ops/prompts')&&request.method()==='POST'){
      promptBody=JSON.parse(request.postData());prompts=[{id:'prompt-1',...promptBody,version:1,active:true,owner_id:'1',created_at:'2026-09-20T10:00:00Z'}]
      return route.fulfill({json:prompts[0]})
    }
    if(path.endsWith('/model-ops/runtime'))return route.fulfill({json:{source:providers.length?'tenant':'environment',model:providers[0]?.model||'deepseek-chat',prompt_version:prompts.length?'sql_generation-v1':'sql-generator-v2',credential_configured:providers[0]?.credential_configured||false}})
    return route.fulfill({status:404,json:{detail:'not mocked'}})
  })
  await page.goto('/workspace')
  await page.getByPlaceholder('用户名').fill('admin');await page.getByPlaceholder('密码').fill('route-password');await page.getByRole('button',{name:'登录'}).click()
  await page.getByRole('button',{name:'模型运营'}).click()
  await page.getByLabel('名称',{exact:true}).fill('DeepSeek Primary')
  await page.getByLabel('模型标识').fill('deepseek-chat')
  await page.getByLabel('密钥环境变量').fill('TENANT_MODEL_API_KEY')
  await page.getByLabel('设为默认').check()
  await page.getByRole('button',{name:'注册提供商'}).click()
  await expect.poll(()=>providerBody).not.toBeNull()
  expect(providerBody.api_key_env).toBe('TENANT_MODEL_API_KEY')
  expect(providerBody).not.toHaveProperty('api_key')
  await expect(page.getByText('DeepSeek Primary')).toBeVisible()
  await page.getByRole('button',{name:'检查'}).click()
  await expect(page.getByText('健康',{exact:true})).toBeVisible()
  await page.getByLabel('系统提示词').fill('优先使用清晰的业务字段别名。')
  await page.getByRole('button',{name:'创建版本'}).click()
  await expect.poll(()=>promptBody).toMatchObject({name:'sql_generation',activate:true})
  await expect(page.getByText('sql_generation v1')).toBeVisible()
  await expect(page.getByText('已激活',{exact:true})).toBeVisible()
  await page.setViewportSize({width:390,height:844})
  await expect(page.locator('.model-ops-page')).toBeVisible()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true)
})
