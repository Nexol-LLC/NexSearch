/* Real Electron UI and native file IPC, isolated demo host only. */
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs/promises');
const {_electron}=require('playwright');
const root=path.resolve(__dirname,'../../..'),out=path.join(root,'preview/qa161-ui');let app,page;
(async()=>{
 await fs.mkdir(out,{recursive:true});const ready=JSON.parse(await fs.readFile(path.join(root,'network/.runtime/preview16/ready.json'),'utf8'));
 const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
 app=await _electron.launch({executablePath:path.join(root,'preview/HostWindows/desktop/runtime/electron.exe'),args:[path.join(root,'desktop/app'),'--url='+ready.employee,'--profile='+out,'--hidden'],env});
 page=await app.firstWindow();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await app.evaluate(({BrowserWindow})=>{for(const w of BrowserWindow.getAllWindows()){w.webContents.setBackgroundThrottling(false);w.setSize(1440,960);w.showInactive()}});
 // Hold initial catalog loading while the user chooses another section.
 await page.route('**/api/catalog',async route=>{await new Promise(r=>setTimeout(r,700));await route.continue()});
 await page.reload();await page.waitForSelector('#workspace:not([hidden])');await page.click('[data-view="carriers"]');
 await page.waitForSelector('[data-carrier15]');await page.waitForTimeout(900);
 assert.equal(await page.evaluate(()=>view),'carriers');assert(await page.locator('#kanban').isHidden());await page.unroute('**/api/catalog');
 // Mock only the operating system save dialog in this test process; exercise real IPC and writeFile.
 for(const section of ['carriers','clients','search']){
  await page.click('[data-view="'+section+'"]');
  await page.waitForFunction(s=>document.querySelector('#export-link').getAttribute('href')?.includes(s==='search'?'leads':s),section);
  const target=path.join(out,section+'.xlsx');await fs.rm(target,{force:true});
  await app.evaluate(({dialog},file)=>{dialog.showSaveDialog=async()=>({canceled:false,filePath:file})},target);
  await page.click('#export-link');
  for(let n=0;n<100;n++){try{if((await fs.stat(target)).size>100)break}catch{}await page.waitForTimeout(50)}
  const bytes=await fs.readFile(target);assert.equal(bytes.subarray(0,2).toString(),'PK');
 }
 await app.evaluate(({dialog})=>{dialog.showSaveDialog=async()=>({canceled:true})});
 await page.click('#export-link');await page.waitForFunction(()=>document.querySelector('#notice')?.textContent==='Сохранение отменено');
 await page.route('**/api/export/**',route=>route.fulfill({status:503,json:{error:'Учебный обрыв связи'}}));
 await page.click('#export-link');await page.waitForFunction(()=>document.querySelector('#notice')?.textContent.includes('Учебный обрыв связи'));
 await page.unroute('**/api/export/**');
 await page.route('**/api/clients?*',route=>route.abort('connectionreset'));
 await page.click('[data-view="clients"]');await page.waitForSelector('#notice.error:not([hidden])');
 await page.click('[data-view="carriers"]');await page.waitForSelector('[data-carrier15]');assert(await page.locator('#kanban').isHidden());
 await page.unroute('**/api/clients?*');await page.click('[data-view="clients"]');await page.waitForSelector('.kanban-card');
 console.log('Startup navigation and real native XLSX writes for carriers, clients and search: passed.');
 await page.click('[data-view="mail"]');await page.waitForSelector('#mail-items16 [data-mail-id16]');
 assert(await page.locator('#mail-folders16').isVisible());assert(await page.locator('#mail-alerts-side16').isVisible());
 await page.screenshot({path:path.join(out,'mail-list.png'),animations:'disabled'});
 await page.locator('[data-mail-id16]').first().click();await page.waitForSelector('.mail-html-frame16');
 assert((await page.frameLocator('.mail-html-frame16').locator('table').innerText()).includes('Самара'));
 assert.equal(await page.frameLocator('.mail-html-frame16').locator('script,img[src^="http"]').count(),0);
 assert.equal(await page.evaluate(()=>window.mailAttack),undefined);
 const before=await page.locator('.mail-html-frame16').elementHandle();await page.evaluate(()=>loadMail16());
 assert(await before.evaluate(el=>el.isConnected)); // Background refresh never replaces an open letter.
 await page.click('[data-mail-format16="text"]');assert((await page.locator('.mail-body-text16').innerText()).includes('Самара'));
 await page.click('[data-mail-format16="html"]');await page.screenshot({path:path.join(out,'mail-html.png'),animations:'disabled'});
 for(const mode of ['text','html','auto']){
  await page.click('#mail-settings16');await page.click('[data-mail-settings-tab16="display"]');
  await page.selectOption('#mail-settings-mode16',mode);await page.fill('#mail-settings-signature16','С уважением, Анна');
  if(mode==='auto')await page.screenshot({path:path.join(out,'mail-settings.png'),animations:'disabled'});
  await page.click('#save-editor');await page.waitForSelector('#editor',{state:'hidden'});
  if(mode==='text'){assert(await page.locator('.mail-body-text16').isVisible());assert.equal(await page.locator('.mail-html-frame16').count(),0)}
  else assert(await page.locator('.mail-html-frame16').isVisible());
  const prefs=await page.evaluate(async()=> (await api('/api/mail/accounts')).accounts[0].settings);assert.equal(prefs.read_mode,mode);
 }
 await page.click('#mail-compose16');await page.waitForSelector('#compose-text16');assert.equal(await page.inputValue('#compose-text16'),'С уважением, Анна');await page.click('#close-editor');
 await page.click('#mail-back16');await page.click('#mail-folder-create16');
 const folder='Документы '+Date.now();await page.fill('#mail-new-folder-name16',folder);await page.selectOption('#mail-new-folder-parent16','Clients');
 await page.click('#save-editor');await page.waitForSelector('#editor',{state:'hidden'});
 await page.waitForFunction(name=>document.querySelector('#mail-folder-title16')?.textContent.includes(name),folder);
 assert((await page.locator('#mail-folders16').innerText()).includes(folder));
 await page.click('[data-folder16="INBOX"]');await page.waitForSelector('#mail-items16 [data-mail-id16]');
 await page.check('#mail-select-all16');await page.click('#mail-mark-unread16');
 await page.waitForFunction(()=>document.querySelector('#mail-selected-count16')?.textContent==='Выбрано: 0');
 await page.click('#mail-unread-filter16');await page.waitForSelector('.mail-row16.unread');
 await page.click('[data-mail-alert-filter16="task"]');assert.equal(await page.locator('#mail-alerts-items16 button small').filter({hasText:'Почта'}).count(),0);
 assert.deepEqual(errors,[]);
 await fs.writeFile(path.join(out,'result.json'),JSON.stringify({version:'1.6.2',passed:true,native_excel:['carriers','clients','search'],errors},null,2));
 console.log('Folders, HTML/text modes, retained reader, signature, bulk flags and notifications: passed. No JavaScript errors.');
})().catch(async e=>{console.error(e);if(page){await page.screenshot({path:path.join(out,'failure.png')});console.error(await page.evaluate(()=>({view,heading:document.querySelector('#page-heading')?.textContent,error:document.querySelector('#editor-error')?.textContent})))}process.exitCode=1}).finally(async()=>{if(app){await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().forEach(w=>w.removeAllListeners('close')));await app.close()}});
