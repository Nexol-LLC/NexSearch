/* Real native UI against the isolated preview cluster; no production records. */
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs/promises');
const {_electron}=require('playwright');
const root=path.resolve(__dirname,'../../..'),out=path.join(root,'preview/qa167-board');
(async()=>{
 await fs.mkdir(out,{recursive:true});
 const ready=JSON.parse(await fs.readFile(path.join(root,'network/.runtime/preview16/ready.json'),'utf8'));
 const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
 let app,page;const errors=[];
 async function launchRole(role){
  if(app){await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().forEach(w=>w.removeAllListeners('close')));await app.close()}
  app=await _electron.launch({executablePath:path.join(root,'preview/HostWindows/desktop/runtime/electron.exe'),args:[path.join(root,'desktop/app'),'--url='+ready[role],'--profile='+path.join(out,role),'--hidden'],env});
  page=await app.firstWindow();page.on('pageerror',e=>errors.push(e.message));
  await app.evaluate(({BrowserWindow})=>{for(const w of BrowserWindow.getAllWindows())w.webContents.setBackgroundThrottling(false)});
  await page.waitForSelector('#workspace:not([hidden])');
 }
 try{
  await launchRole('manager');
  const ids=await page.evaluate(async inn=>{
   const people=(await api('/api/admin/employees')).employees;
   const colleague=people.find(p=>p.first_name==='Иван');
   const original=(await api('/api/clients/overview')).companies[0];
   await api('/api/companies/'+original.id+'/transfer',{method:'POST',body:JSON.stringify({employee_id:colleague.id,revision:original.revision,note:'Isolated kanban test'})});
   const own=(await api('/api/search/inn',{method:'POST',body:JSON.stringify({inn})})).company;
   await api('/api/leads/'+own.id,{method:'PATCH',body:JSON.stringify({status:'taken',called:true,call_note:'Isolated manager client',revision:own.revision})});
   return {colleague:colleague.id,company:original.id,own:own.id};
  },ready.inn);
  await page.click('[data-view="clients"]');await page.waitForFunction(()=>document.querySelectorAll('#kanban .kanban-card').length===4);
  assert.equal(await page.inputValue('#client-owner167'),'');
  assert((await page.locator('#client-owner167').innerText()).includes('Все сотрудники отдела'));
  assert.equal(await page.locator('#kanban .client-owner167').count(),4);
  assert.equal(await page.locator('#client-count').innerText(),'4');
  assert((await page.locator('#kanban').innerText()).includes('Иван Орлов'));
  assert((await page.locator('#kanban').innerText()).includes('Анна Петрова'));
  assert(await page.locator('#stages-edit').isHidden());
  await page.screenshot({path:path.join(out,'manager-all-light.png'),animations:'disabled'});
  await page.selectOption('#client-owner167',ids.colleague);await page.waitForFunction(()=>document.querySelectorAll('#kanban .kanban-card').length===1);
  assert.equal(await page.locator('#kanban .kanban-card').getAttribute('draggable'),'false');
  await page.locator('#kanban .kanban-card').click();await page.waitForSelector('#client-transfer162');
  assert(await page.locator('#company-history162').isVisible());await page.click('#close-editor');
  await page.selectOption('#client-owner167','mine');await page.waitForFunction(id=>document.querySelector('#kanban .kanban-card')?.dataset.id===id,ids.own);
  assert.equal(await page.locator('#kanban .kanban-card').getAttribute('draggable'),'true');assert(await page.locator('#stages-edit').isVisible());
  assert((await page.locator('#page-meta').innerText()).includes('Ваши клиенты'));
  await page.selectOption('#client-owner167','');await page.waitForFunction(()=>document.querySelectorAll('#kanban .kanban-card').length===4);
  await page.evaluate(()=>applyAppearance162({theme:'dark',motion:false},true));
  await page.waitForTimeout(350);
  await page.screenshot({path:path.join(out,'manager-all-dark.png'),animations:'disabled'});
  // A delayed aggregate response must never populate the carriers tab.
  await page.route('**/api/clients/overview?*',async route=>{await new Promise(r=>setTimeout(r,450));await route.continue()});
  await page.selectOption('#client-owner167',ids.colleague);await page.click('[data-view="carriers"]');
  await page.waitForSelector('[data-carrier15]');await page.waitForTimeout(650);
  assert(await page.locator('#kanban').isHidden());assert(await page.locator('#client-scope167').isHidden());
  assert((await page.locator('#table-body').innerText()).includes('ДЕМО Перевозчик'));
  await page.unroute('**/api/clients/overview?*');
  await launchRole('employee');await page.click('[data-view="clients"]');
  await page.waitForFunction(()=>document.querySelectorAll('#kanban .kanban-card').length===2);
  assert(await page.locator('#client-scope167').isHidden());
  assert.equal(await page.evaluate(async()=>{try{await api('/api/clients/overview');return 200}catch(e){return e.status}}),403);
  await launchRole('director');await page.click('[data-view="clients"]');
  await page.waitForFunction(()=>document.querySelectorAll('#kanban .kanban-card').length===4);
  assert.equal(await page.inputValue('#client-owner167'),'');assert((await page.locator('#client-owner167').innerText()).includes('Все сотрудники'));
  assert.deepEqual(errors,[]);
  console.log('Leadership default aggregate board, owner filter, personal stages, transfer/timeline, light/dark, delayed navigation and employee privacy passed.');
  await fs.writeFile(path.join(out,'result.json'),JSON.stringify({passed:true,roles:['manager','director','logist'],errors,screenshots:['manager-all-light.png','manager-all-dark.png']},null,2));
 }catch(error){await page.screenshot({path:path.join(out,'failure.png')});throw error}
 finally{await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().forEach(w=>w.removeAllListeners('close')));await app.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
