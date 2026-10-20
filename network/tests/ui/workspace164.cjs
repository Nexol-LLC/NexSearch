const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs/promises');
const {_electron}=require('playwright');
const root=path.resolve(__dirname,'../../..'),out=path.join(root,'preview/qa164-ui');let app,page;let errors=[];
(async()=>{
 await fs.mkdir(out,{recursive:true});const ready=JSON.parse(await fs.readFile(path.join(root,'network/.runtime/preview16/ready.json'),'utf8'));
 const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
 const launch=async role=>{
  if(app){await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().forEach(w=>w.removeAllListeners('close')));await app.close()}
  app=await _electron.launch({executablePath:path.join(root,'preview/HostWindows/desktop/runtime/electron.exe'),args:[path.join(root,'desktop/app'),'--url='+ready[role],'--profile='+path.join(out,role+'-'+Date.now()),'--hidden'],env});
  page=await app.firstWindow();page.on('pageerror',e=>errors.push(e.message));await app.evaluate(({BrowserWindow,Notification})=>{for(const w of BrowserWindow.getAllWindows()){w.webContents.setBackgroundThrottling(false);w.setSize(1480,1000);w.showInactive()}Notification.prototype.show=function(){globalThis.lastNexNotification={title:this.title,body:this.body};};});
  await page.waitForSelector('#workspace:not([hidden])');await page.evaluate(()=>refreshNotifications16());
 };
 await launch('employee');await page.evaluate(()=>applyAppearance162({theme:'light',density:'comfortable',motion:true},true));
 await page.click('[data-view="carriers"]');await page.waitForSelector('[data-open-carrier15]');await page.locator('[data-open-carrier15]').first().click();
 assert.deepEqual(await page.locator('#editor .form-grid').first().locator('input').evaluateAll(inputs=>inputs.map(i=>i.id).slice(0,2)),['carrier-name','carrier-inn']);
 await page.fill('#carrier-inn',ready.inn);await page.screenshot({path:path.join(out,'carrier-inn-light.png')});await page.click('#save-editor');await page.waitForSelector('#editor',{state:'hidden'});
 await page.fill('#search-input',ready.inn);assert.equal(await page.locator('[data-carrier15]').count(),1);assert((await page.locator('#table-body').innerText()).includes(ready.inn));
 await page.evaluate(async()=>{await api('/api/notifications/clear',{method:'POST',body:'{}'});await refreshNotifications16(true)});
 const task=await page.evaluate(async()=>api('/api/tasks',{method:'POST',body:JSON.stringify({title:'Проверка просрочки 1.6.4',description:'ДЕМО связаться с клиентом',due_at:new Date(Date.now()-120000).toISOString()})}));
 await page.evaluate(()=>refreshNotifications16());await page.waitForSelector('.notification-popup164');assert((await page.locator('#notification-popups164').innerText()).includes(task.title));
 const popups=await page.locator('.notification-popup164').count();await page.evaluate(()=>refreshNotifications16());assert.equal(await page.locator('.notification-popup164').count(),popups);
 await page.screenshot({path:path.join(out,'popup-light.png')});await page.locator('.notification-popup164 .popup-open164').first().click();await page.waitForSelector('#task-title');assert.equal(await page.inputValue('#task-title'),task.title);await page.click('#close-editor');
 await page.click('#notifications-bell16');await page.waitForSelector('[data-delete-alert164]');await page.locator('[data-delete-alert164]').first().click();await page.waitForFunction(()=>!document.querySelector('[data-delete-alert164]'));
 await page.click('#alerts-clear164');await page.evaluate(()=>refreshNotifications16(true));assert.equal(await page.locator('[data-alert16]').count(),0);await page.click('#alerts-close16');
 assert.deepEqual(errors,[]);
 // A failed iframe request recovers, then tab switching preserves an unsaved plan.
 let requests=0;await page.route('**/pack/?*',route=>{requests++;return requests===1?route.fulfill({status:503,contentType:'text/html',body:'Временная ошибка загрузки'}):route.continue()});
 await page.click('[data-view="pack"]');await page.waitForFunction(()=>document.querySelector('#pack-frame').contentWindow.NexPackReady===true,{timeout:35000});
 assert.equal(requests,2);await page.waitForSelector('#pack-status164',{state:'hidden'});
 const pack=page.frameLocator('#pack-frame');await pack.locator('[data-simple-step="cargo"]').click();await pack.locator('.simple-name15').first().fill('Несохранённый груз 164');
 for(let i=0;i<3;i++){await page.click('[data-view="clients"]');await page.click('[data-view="pack"]')}
 assert.equal(await pack.locator('.simple-name15').first().inputValue(),'Несохранённый груз 164');
 const dimensions=await pack.locator('#scene canvas').evaluate(c=>({w:c.width,h:c.height,css:c.getBoundingClientRect().width}));assert(dimensions.w>100&&dimensions.h>100&&dimensions.css>100);
 await page.evaluate(()=>applyAppearance162({...appearance162,theme:'dark'},true));await page.screenshot({path:path.join(out,'pack-ready-dark.png')});
 await page.unroute('**/pack/?*');assert.deepEqual(errors,[]);
 await launch('manager');await page.evaluate(()=>refreshNotifications16());
 const escalations=await page.evaluate(()=>alertsData16.filter(n=>n.kind==='task.overdue'));assert(escalations.some(n=>n.entity_id===task.id));
 await page.evaluate(()=>applyAppearance162({theme:'dark',density:'comfortable',motion:true},true));await page.click('#notifications-bell16');
 const escalationId=escalations.find(n=>n.entity_id===task.id).id;await page.locator('[data-alert16="'+escalationId+'"]').click();await page.waitForSelector('#task-title');assert.equal(await page.inputValue('#task-title'),task.title);
 await page.evaluate(()=>{clearPopups164();popup164({...alertsData16.find(n=>n.kind==='task.overdue'),id:crypto.randomUUID()},user.id)});
 await page.locator('.notification-popup164 .popup-close164').click();assert(await page.locator('#editor').isVisible());await page.click('#close-editor');
 await page.evaluate(async()=>{const item=alertsData16.find(n=>n.kind==='task.overdue');popup164({...item,id:crypto.randomUUID()},user.id)});await page.screenshot({path:path.join(out,'manager-popup-dark.png')});
 // Native IPC rejects unbounded/untrusted payloads instead of accepting arbitrary content.
 assert.equal(await page.evaluate(async()=>{try{await NexDesktop.notify({id:'bad',owner:user.id,title:'x',body:'y'});return false}catch{return true}}),true);
 assert.deepEqual(errors,[]);await fs.writeFile(path.join(out,'result.json'),JSON.stringify({version:'1.6.4',passed:true,tests:['carrier INN saved and searchable','clickable popup deduplication','individual and full notification deletion','supervisor overdue alert opens task','Pack startup retry after HTTP 503','unsaved cargo survives tab switching','nonzero canvas after tab switching','light and dark themes','bounded native notification IPC']},null,2));
 console.log('1.6.4 native UI: carriers, notifications, supervision and Pack recovery passed.');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{if(app){await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().forEach(w=>w.removeAllListeners('close')));await app.close()}});
