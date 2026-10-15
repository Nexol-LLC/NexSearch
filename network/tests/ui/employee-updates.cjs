/* Isolated real Chromium rendering, without an employee profile or live host. */
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs/promises');
const {_electron}=require('playwright');
const root=path.resolve(__dirname,'../../..');
(async()=>{
 const fixture=path.join(root,'preview/update-ui-fixture.cjs');
 await fs.writeFile(fixture,"const {app,BrowserWindow}=require('electron');app.whenReady().then(()=>new BrowserWindow({show:false,webPreferences:{sandbox:true}}).loadURL('about:blank'));app.on('window-all-closed',()=>app.quit());");
 const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
 const app=await _electron.launch({executablePath:path.join(root,'preview/HostWindows/desktop/runtime/electron.exe'),args:[fixture],env});
 const page=await app.firstWindow(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 try{
  await app.evaluate(({webContents})=>webContents.getAllWebContents().forEach(w=>w.setBackgroundThrottling(false)));
  await page.setContent('<div class="session-actions"></div><div class="brand"><small>версия 1.5.0</small></div><main></main>');
  await page.evaluate(()=>{
   window.$=s=>document.querySelector(s);window.dialogOpen=true;
   window.esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
   window.detail=(label,value)=>`<p>${esc(label)}: ${esc(value)}</p>`;window.dateFull=s=>s;
   window.openEditor=(title,body)=>{$('main').innerHTML=body};
   window.testState={installed_version:'1.5.0',version:'1.5.1',message:'Проверяем пакет сотрудника',checking:true};
   window.statusCalls=0;window.queueResult=true;
   window.api=async(url)=>{
    if(url.endsWith('/status')&&url.includes('/client/')){statusCalls++;return {...testState}}
    if(url.endsWith('/check'))return {queued:queueResult};
    return {hostname:'test-host',version:'1.5.1',ip:'127.0.0.1',cpu_percent:1,memory_used_bytes:1,memory_total_bytes:2,memory_percent:50,disk_free_bytes:100,queue:{running:0,queued:0},updates:{}};
   };
   window.fetch=async()=>({json:async()=>({host_url:'https://test-host'})});
  });
  await page.addScriptTag({content:await fs.readFile(path.join(root,'network/web/settings_hosted.js'),'utf8')});
  await page.getByText('Настройки',{exact:true}).click();await page.click('#settings-update-tab');
  await page.waitForFunction(()=>document.querySelector('#update-details').textContent.includes('1.5.0'));
  assert(await page.locator('#check-update-now').isDisabled());
  await page.evaluate(()=>testState={installed_version:'1.5.0',version:'1.5.1',phase:'ready',pending_state:'stage',message:'ready',checking:false});
  await page.waitForFunction(()=>document.querySelector('#update-details').textContent.includes('Полностью закройте'));
  assert(!(await page.locator('#check-update-now').isDisabled()));
  assert((await page.locator('#update-details').innerText()).includes('1.5.1'));
  await page.evaluate(()=>queueResult=false);await page.click('#check-update-now');
  await page.waitForFunction(()=>document.querySelector('#update-request-message').textContent.includes('недоступен'));
  await page.evaluate(()=>testState={installed_version:'1.5.0',phase:'error',message:'Установка не завершена',last_error:'PermissionError',pending_state:'stage'});
  await page.waitForFunction(()=>document.querySelector('#update-details').textContent.includes('PermissionError'));
  assert(!(await page.locator('#update-details').innerText()).includes('Обновление готово'));
  // An old host response must not overwrite the Updates tab after switching.
  await page.evaluate(()=>{
   const original=api;window.api=url=>url==='/api/host/status'?new Promise(resolve=>{window.resolveHost=()=>original(url).then(resolve)}):original(url);
  });
  await page.click('#settings-host-tab');await page.click('#settings-update-tab');await page.evaluate(()=>resolveHost());
  await page.waitForFunction(()=>document.querySelector('#update-details')?.textContent.includes('PermissionError'));
  assert.deepEqual(errors,[]);
  console.log('Automatic polling, separate versions, queued=false, install errors and tab request isolation passed.');
 }finally{await app.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
