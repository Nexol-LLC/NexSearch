const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs/promises');
const {_electron}=require('playwright');
const root=path.resolve(__dirname,'../../..'),out=path.join(root,'preview/qa166-ui');let app,page;const errors=[];
(async()=>{
 await fs.mkdir(out,{recursive:true});const ready=JSON.parse(await fs.readFile(path.join(root,'network/.runtime/preview16/ready.json'),'utf8'));
 const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
 app=await _electron.launch({executablePath:path.join(root,'preview/HostWindows/desktop/runtime/electron.exe'),args:[path.join(root,'desktop/app'),'--url='+ready.employee,'--profile='+path.join(out,'employee-'+Date.now()),'--hidden'],env});
 page=await app.firstWindow();page.on('pageerror',e=>errors.push(e.message));await app.evaluate(({BrowserWindow})=>{for(const w of BrowserWindow.getAllWindows()){w.webContents.setBackgroundThrottling(false);w.setSize(1480,1000);w.showInactive()}});
 await page.waitForSelector('#workspace:not([hidden])');await page.waitForFunction(()=>!document.querySelector('.topbar [data-transport16]').disabled);
 assert.deepEqual(await page.locator('.topbar [data-transport16] option').evaluateAll(nodes=>nodes.map(n=>n.value)),['auto','lan','internet','vpn']);
 await page.locator('.topbar [data-transport16]').selectOption('internet');
 await page.waitForFunction(()=>!document.querySelector('.topbar [data-transport16]').disabled);
 const missing=await page.evaluate(()=>api('/api/client/transport'));assert.equal(missing.preference,'internet');assert.equal(missing.host_found,false);assert.equal(missing.internet_configured,false);
 assert.equal(await page.evaluate(async()=>{try{await api('/api/state');return 200}catch(e){return e.status}}),503);
 assert.equal(await page.evaluate(async()=>(await api('/api/client/vpn/status')).config.enabled),false);
 await page.locator('.topbar [data-transport16]').selectOption('lan');await page.waitForFunction(()=>!document.querySelector('.topbar [data-transport16]').disabled);assert.equal((await page.evaluate(()=>api('/api/client/transport'))).transport,'lan');
 await page.route('**/api/me',route=>route.fulfill({status:503,json:{error:'Simulated startup outage'}}));await page.reload();await page.waitForSelector('#auth-screen:not([hidden])');
 await page.unroute('**/api/me');await page.waitForSelector('#workspace:not([hidden])',{timeout:15000});
 await page.click('.sidebar-settings162');await page.waitForSelector('#settings-internet166');await page.click('#settings-internet166');await page.waitForFunction(()=>document.querySelector('#internet-state166')?.textContent.includes('администратор'));
 assert(await page.locator('#internet-use166').isDisabled());assert((await page.locator('#internet-url166').innerText()).includes('не установлен'));
 await page.evaluate(()=>applyAppearance162({theme:'light',density:'comfortable',motion:false},true));await page.screenshot({path:path.join(out,'internet-not-configured-light.png'),animations:'disabled'});
 // Presentational state only: no real public endpoint is fabricated or contacted.
 await page.route('**/api/client/transport',route=>route.request().method()==='GET'?route.fulfill({json:{preference:'auto',transport:'internet',host_found:true,host_url:'https://gateway.example.org',internet_configured:true,internet_online:true,internet_url:'https://gateway.example.org',internet_checked_at:new Date().toISOString(),internet_error:''}}):route.continue());
 await page.click('#internet-recheck166');await page.waitForFunction(()=>document.querySelector('#internet-url166').textContent==='https://gateway.example.org');assert(!(await page.locator('#internet-use166').isDisabled()));
 await page.evaluate(()=>applyAppearance162({...appearance162,theme:'dark'},true));await page.screenshot({path:path.join(out,'internet-online-dark-mock.png'),animations:'disabled'});
 await page.unroute('**/api/client/transport');await page.click('#close-editor');await page.evaluate(()=>refreshTransport16());
 assert.deepEqual(errors,[]);await fs.writeFile(path.join(out,'result.json'),JSON.stringify({passed:true,version:'1.6.6',checks:['internet choice does not silently fall back to LAN','no VPN enrollment','LAN restored','missing profile explained','connection settings','light/dark states'],presentationOnlyOnlineState:true},null,2));console.log('Internet transport selector, missing profile, forced mode, LAN restore and settings passed.');
})().catch(async e=>{console.error(e);if(page)await page.screenshot({path:path.join(out,'failure.png')}).catch(()=>{});process.exitCode=1}).finally(async()=>{if(app){try{await page.evaluate(()=>api('/api/client/transport',{method:'POST',body:JSON.stringify({mode:'lan'})}))}catch{}await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().forEach(w=>w.removeAllListeners('close')));await app.close()}});
