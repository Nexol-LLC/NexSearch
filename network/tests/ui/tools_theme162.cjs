/* Run against preview16.py's isolated host. External sites are not contacted. */
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs/promises');
const {_electron}=require('playwright');
const root=path.resolve(__dirname,'../../..'),out=path.join(root,'preview/qa162-tools');
let app;
(async()=>{
 await fs.mkdir(out,{recursive:true});
 const ready=JSON.parse(await fs.readFile(path.join(root,'network/.runtime/preview16/ready.json'),'utf8'));
 const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
 app=await _electron.launch({executablePath:path.join(root,'preview/HostWindows/desktop/runtime/electron.exe'),args:[path.join(root,'desktop/app'),'--url='+ready.employee,'--profile='+out,'--hidden'],env});
 const page=await app.firstWindow();console.log('Native window ready');
 const bounds=[];
 await app.evaluate(({ipcMain,BrowserWindow})=>{
  ipcMain.removeHandler('service:show');
  ipcMain.handle('service:show',(_event,_key,rect)=>{global.toolsThemeBounds=rect;return true});
  const win=BrowserWindow.getAllWindows()[0];win.webContents.setBackgroundThrottling(false);win.showInactive();
 });
 const errors=[];page.setDefaultTimeout(20000);page.on('pageerror',e=>errors.push(e.message));
 await page.waitForSelector('#workspace:not([hidden])');console.log('Workspace ready');
 const contrast=async selector=>page.locator(selector).evaluate(el=>{
  const rgb=s=>(s.match(/[\d.]+/g)||[]).map(Number);
  const lum=c=>c.slice(0,3).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
  const css=getComputedStyle(el);let parent=el,bg;
  while(parent){bg=rgb(getComputedStyle(parent).backgroundColor);if(bg.length===3||bg[3]>0)break;parent=parent.parentElement}
  const a=lum(rgb(css.color)),b=lum(bg);
  return {ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05),color:css.color,background:css.backgroundColor,text:el.textContent};
 });
 for(const theme of ['dark','light']){
  await page.evaluate(theme=>applyAppearance162({theme,density:'comfortable',motion:false},true),theme);
  for(const [view,services] of Object.entries({maps:['yandex','2gis','cargo','scales'],rates:['calculator','signl'],tools:['clock','tracking','pdf','optimus']})){
   console.log('Checking',theme,view);await page.click(`[data-view="${view}"]`);
   await page.waitForSelector('#service-switcher button');
   assert.equal(await page.locator('#service-switcher').evaluate(el=>getComputedStyle(el).backgroundColor),theme==='dark'?'rgb(25, 33, 48)':'rgb(255, 255, 255)');
   for(const service of services){
    const selector=`#service-switcher [data-service="${service}"]`;
    await page.click(selector);
    assert(await page.locator(selector).evaluate(el=>el.classList.contains('active')));
    await page.mouse.move(10,10);
    for(const button of await page.locator('#service-switcher button').all()){
     const result=await contrast(`[data-service="${await button.getAttribute('data-service')}"]`);
     assert(result.ratio>=4.5,JSON.stringify({theme,view,...result}));
    }
    await page.locator(selector).hover();assert((await contrast(selector)).ratio>=4.5);
    const other=page.locator('#service-switcher button:not(.active)').first();await other.hover();
    assert((await contrast(`[data-service="${await other.getAttribute('data-service')}"]`)).ratio>=4.5);
    await page.locator(selector).focus();await page.keyboard.press('Tab');
    for(const control of ['#service-back','#service-reload','#service-browser'])assert((await contrast(control)).ratio>=4.5);
   }
   const rect=await app.evaluate(()=>global.toolsThemeBounds),sidebar=await page.locator('#service-switcher').boundingBox(),toolbar=await page.locator('.service-tools').boundingBox();
   assert(rect.x>=sidebar.x+sidebar.width-1&&rect.y>=toolbar.y+toolbar.height-1);bounds.push({theme,view,rect});
   await page.evaluate(()=>document.querySelector('#remote-fallback').hidden=false);
   for(const selector of ['#remote-fallback h2','#remote-fallback p'])assert((await contrast(selector)).ratio>=4.5);
   await page.screenshot({path:path.join(out,view+'-'+theme+'.png'),animations:'disabled'});
  }
 }
 await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].setSize(1100,820));
 await page.evaluate(()=>applyAppearance162({theme:'dark',density:'compact',motion:false},true));
 await page.click('[data-view="maps"]');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:path.join(out,'maps-dark-1100.png'),animations:'disabled'});
 assert.deepEqual(errors,[]);
 await fs.writeFile(path.join(out,'result.json'),JSON.stringify({passed:true,themes:2,services:10,minimumContrast:4.5,bounds},null,2));
 console.log('Maps, rates and tools: both palettes, all 9 services, active/hover text contrast >=4.5, toolbar, fallback, native view bounds and 1100px layout passed.');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{if(app){await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().forEach(w=>w.removeAllListeners('close')));await app.close()}});
