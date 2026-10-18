/* Native settings UI with synthetic onboarding states; never enrolls devices. */
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs/promises');
const {_electron}=require('playwright');
const root=path.resolve(__dirname,'../../..'),out=path.join(root,'preview/qa16-vpn');let app;
(async()=>{
 await fs.mkdir(out,{recursive:true});const errors=[];
 const ready=JSON.parse(await fs.readFile(path.join(root,'network/.runtime/preview16/ready.json'),'utf8'));
 const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
 for(const role of ['employee','director']){
  app=await _electron.launch({executablePath:path.join(root,'preview/HostWindows/desktop/runtime/electron.exe'),args:[path.join(root,'desktop/app'),'--url='+ready[role],'--profile='+path.join(out,role),'--hidden'],env});
  const page=await app.firstWindow();page.on('pageerror',error=>errors.push(error.message));
  let state={phase:'disabled',message:'Удалённое подключение выключено',config:{enabled:false,provider:'tailscale',control_url:'https://controlplane.tailscale.com'}};
  let transport={preference:'auto',transport:'lan',host_found:true,host_url:'https://192.168.0.102:8743'};
  await page.route('**/api/client/transport',async route=>{if(route.request().method()==='POST'){const {mode}=route.request().postDataJSON();transport={...transport,preference:mode,transport:mode==='vpn'?null:'lan',host_found:mode!=='vpn'}}await route.fulfill({json:transport})});
  await page.route('**/api/client/vpn/**',async route=>{
   if(route.request().method()==='POST'){const body=route.request().postDataJSON();state={...state,config:body,phase:'needs_login',message:'Требуется однократный вход',auth_url:'https://vpn.example.org/register/synthetic'}}
   await route.fulfill({json:state});
  });
  await page.route('**/api/remote/status',route=>route.fulfill({json:{phase:'disabled',message:'VPN хоста выключен',config:{enabled:false,provider:'tailscale',control_url:'https://controlplane.tailscale.com'}}}));
  await page.waitForSelector('#workspace:not([hidden])',{timeout:30000});
  await page.evaluate(()=>openHostSettings());await page.click('#settings-vpn-tab16');
  await page.waitForSelector('#vpn-connect16');
  assert.equal(await page.locator('#vpn-host-panel16').count(),role==='director'?1:0);
  await page.locator('#transport-panel16 [data-transport16]').selectOption('vpn');
  await page.waitForFunction(()=>document.querySelector('#transport-panel16 [data-transport16]')?.value==='vpn');
  assert.equal(transport.host_found,false);
  await page.waitForFunction(()=>!document.querySelector('#transport-panel16 [data-transport16]').disabled);
  await page.locator('#transport-panel16 [data-transport16]').selectOption('lan');
  await page.waitForFunction(()=>document.querySelector('#transport-panel16 [data-transport16]')?.value==='lan');
  await page.selectOption('#vpn-provider16','headscale');await page.fill('#vpn-control16','https://vpn.example.org');
  await page.waitForTimeout(2300);assert.equal(await page.inputValue('#vpn-control16'),'https://vpn.example.org');
  await page.click('#vpn-connect16');await page.waitForSelector('#vpn-login16:not([hidden])');
  assert.equal(state.config.control_url,'https://vpn.example.org');
  assert.equal(await page.locator('#vpn-login16').getAttribute('href'),'https://vpn.example.org/register/synthetic');
  await page.screenshot({path:path.join(out,role+'-vpn.png'),animations:'disabled'});
  await page.evaluate(()=>closeEditor());
  await page.evaluate(()=>{user=null;$('#workspace').hidden=true;$('#auth-screen').hidden=false});
  await page.waitForSelector('#vpn-auth-login16:not([hidden])',{timeout:10000});
  await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().forEach(w=>w.removeAllListeners('close')));await app.close();app=null;
 }
 assert.deepEqual(errors,[]);console.log('VPN native UI: strict transport switch, employee/director settings, editable Headscale address, login link and pre-login onboarding passed. No JS errors.');
})().catch(error=>{console.error(error);process.exitCode=1}).finally(async()=>{if(app){await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().forEach(w=>w.removeAllListeners('close')));await app.close()}});
