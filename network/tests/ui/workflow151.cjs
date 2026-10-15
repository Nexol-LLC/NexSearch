/* Local preview integration: browser contact requests use in-memory fixtures. */
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs/promises');
const {_electron}=require('playwright');
const root=path.resolve(__dirname,'../../..');
(async()=>{
 const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
 const app=await _electron.launch({executablePath:path.join(root,'preview/HostWindows/desktop/runtime/electron.exe'),args:[path.join(root,'desktop/app'),'--url=http://127.0.0.1:8898/','--profile='+path.join(root,'preview/qa-workflow151'),'--hidden'],env});
 const page=await app.firstWindow(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await app.evaluate(({webContents})=>webContents.getAllWebContents().forEach(contents=>contents.setBackgroundThrottling(false)));
 try{
  await page.waitForSelector('#workspace:not([hidden])',{timeout:30000});
  assert.equal(await page.locator('.brand small').innerText(),'версия 1.5.1');
  assert.equal(await page.locator('[data-view="orders"]').count(),0);
  assert.equal(await page.getByText('Активные заявки',{exact:true}).count(),0);
  const id='00000000-0000-4000-8000-000000000151';let contactsCalls=0,leadBody=null;
  await page.route('**/api/leads/'+id+'/contacts',async route=>{
   const body=route.request().postDataJSON();contactsCalls++;
   assert.deepEqual(body,{phones:['+7 900 555-15-15'],emails:['working@example.org'],revision:0});
   await route.fulfill({json:{phones:body.phones,emails:body.emails,contacts_revision:1}});
  });
  await page.route('**/api/leads/'+id,async route=>{leadBody=route.request().postDataJSON();await route.fulfill({json:{revision:1}})});
  await page.evaluate(id=>{view='search';const company={id,employee_id:user.id,name:'ДЕМО · контакты 1.5.1',inn:'9650000013',phones:['+7 495 100-10-10'],emails:['source@example.org'],lead_status:'new',revision:0};rows=[company];render();editCompany(company)},id);
  assert.equal(await page.locator('#client-order-new').count(),0);
  await page.fill('#edit-note','Черновик описания звонка сохраняется');
  await page.fill('#working-phones151','+7 900 555-15-15');await page.fill('#working-emails151','working@example.org');
  await page.click('#save-contacts151');await page.waitForFunction(()=>document.querySelector('#contacts-state151').textContent==='Контакты сохранены');
  assert.equal(await page.locator('#edit-note').inputValue(),'Черновик описания звонка сохраняется');
  assert.equal(await page.locator('#editor-body > .detail [data-copy="+7 900 555-15-15"]').count(),1);
  assert.equal(await page.locator('#editor-body > .detail [data-copy="working@example.org"]').count(),1);
  assert((await page.locator('#table-body').innerText()).includes('+7 900 555-15-15'));
  assert((await page.locator('#table-body').innerText()).includes('working@example.org'));
  await page.screenshot({path:path.join(root,'preview/contacts151.png'),animations:'disabled'});
  await page.click('#save-editor');await page.waitForSelector('#editor',{state:'hidden'});
  assert.equal(contactsCalls,1);assert.equal(leadBody.call_note,'Черновик описания звонка сохраняется');
  console.log('Editable contacts, primary display and preserved call notes passed.');
  await page.evaluate(()=>{mountService=async()=>{}});
  await page.click('[data-view="tools"]');
  assert.deepEqual(await page.locator('#service-switcher [data-service]').evaluateAll(nodes=>nodes.map(n=>n.dataset.service)),['clock','tracking','optimus']);
  assert.equal(await page.locator('#service-switcher .active').getAttribute('data-service'),'clock');
  console.log('Orders removed; service order and initial service passed.');
  await page.click('[data-view="pack"]');let frame=page.frames().find(frame=>frame.url().includes('/pack/'));
  if(!frame){await page.frameLocator('#pack-frame').locator('[data-simple-step="cargo"]').waitFor();frame=page.frames().find(frame=>frame.url().includes('/pack/'))}
  await frame.waitForSelector('#cargo-length-unit151',{state:'attached'});
  await frame.evaluate(()=>localStorage.removeItem(cargoUnitsKey151()));await frame.goto(frame.url());
  await frame.click('[data-simple-step="cargo"]');
  assert.equal(await frame.locator('#cargo-length-unit151').inputValue(),'m');assert.equal(await frame.locator('#cargo-weight-unit151').inputValue(),'t');
  const first=frame.locator('[data-card15]').first();
  for(const [key,value] of Object.entries({l:'13.6',w:'2.45',h:'2.7',weight:'1.25',qty:'3',tiers:'3'}))await first.locator('.simple-'+key+'15').fill(value);
  const expected={L:13600,W:2450,H:2700,weight:1250,qty:3,tiers:3};
  const actual=()=>frame.evaluate(()=>Object.fromEntries(['L','W','H','weight','qty','tiers'].map(key=>[key,readCargo()[0][key]])));
  assert.deepEqual(await actual(),expected);
  for(const [unit,value] of [['mm','13600'],['cm','1360'],['m','13.6']]){await frame.selectOption('#cargo-length-unit151',unit);assert.equal(await first.locator('.simple-l15').inputValue(),value);assert.deepEqual(await actual(),expected)}
  for(const [unit,value] of [['kg','1250'],['t','1.25']]){await frame.selectOption('#cargo-weight-unit151',unit);assert.equal(await first.locator('.simple-weight15').inputValue(),value);assert.deepEqual(await actual(),expected)}
  await frame.selectOption('#cargo-length-unit151','cm');await frame.selectOption('#cargo-weight-unit151','kg');
  const plan=await frame.evaluate(()=>planSnapshot());await frame.goto(frame.url());await frame.click('[data-simple-step="cargo"]');
  assert.equal(await frame.locator('#cargo-length-unit151').inputValue(),'cm');assert.equal(await frame.locator('#cargo-weight-unit151').inputValue(),'kg');
  await frame.evaluate(plan=>restorePlan(plan),plan);assert.deepEqual(await actual(),expected);
  await page.screenshot({path:path.join(root,'preview/cargo151.png'),animations:'disabled'});
  await first.locator('.simple-tiers15').fill('1.5');await frame.click('#btnCalc');
  assert(await frame.locator('#cargo-errors15').isVisible());
  await first.locator('.simple-tiers15').fill('3');
  assert.deepEqual(errors,[]);console.log('Cargo defaults, conversions, persistent preferences, saved plan and tier validation passed.');
 }finally{
  await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().forEach(window=>window.removeAllListeners('close')));await app.close();
 }
})().catch(error=>{console.error(error);process.exitCode=1});
