'use strict';
const {app,BrowserWindow,WebContentsView,ipcMain,shell,session,dialog,clipboard}=require('electron');
const path=require('path');
app.setName('NexSearch');
if(process.platform==='win32')app.setAppUserModelId('ru.nexsearch.v13');
const argument=key=>process.argv.find(a=>a.startsWith(key+'='))?.slice(key.length+1);
const ui=new URL(argument('--url')||'http://127.0.0.1:8765');
if(ui.protocol!=='http:'||ui.hostname!=='127.0.0.1'||ui.pathname!=='/')throw Error('Invalid local interface');
if(argument('--profile'))app.setPath('userData',argument('--profile'));
const services={calculator:'https://tkblack.ru/calculator/',signl:'https://signl.ru/',yandex:'https://yandex.ru/maps/','2gis':'https://2gis.ru/moscow/directions/tab/truck',scales:'https://nerudas.ru/vesy-platon',cargo:'https://routes.cargo.run/',tracking:'https://1track.ru/tracking',clock:'https://www.timeserver.ru/countries/ru',optimus:'https://optimus.mts.ru/'};
const program=argument('--program'),home=argument('--home'),role=argument('--role')==='host'?'HostWindows':'EmployeeWindows';
let preparedUpdate=null;
let win,active=null,views=new Map(),exiting=false;
function allowUrl(value){try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password;}catch{return false;}}
function external(value){if(allowUrl(value))shell.openExternal(value);}
function bounds(value){if(!value||['x','y','width','height'].some(k=>!Number.isFinite(value[k])))throw Error('Invalid bounds');const [w,h]=win.getContentSize();return {x:Math.max(0,Math.min(w,Math.round(value.x))),y:Math.max(0,Math.min(h,Math.round(value.y))),width:Math.max(1,Math.min(w,Math.round(value.width))),height:Math.max(1,Math.min(h,Math.round(value.height)))};}
function trusted(event){if(event.sender!==win.webContents||event.senderFrame.url!==ui.href)throw Error('Untrusted caller');}
function hide(){if(active){win.contentView.removeChildView(active);active=null;}}
function guard(contents,local=false){
 contents.setWindowOpenHandler(({url})=>{external(url);return {action:'deny'};});
 contents.on('will-navigate',(event,url)=>{const okay=local?new URL(url).origin===ui.origin:allowUrl(url);if(!okay)event.preventDefault();});
 contents.on('will-attach-webview',event=>event.preventDefault());
}
async function show(service,rect){if(!Object.hasOwn(services,service))throw Error('Unknown service');hide();let view=views.get(service);
 if(!view){if(views.size>=2){const [key,old]=views.entries().next().value;old.webContents.close();views.delete(key);}
 view=new WebContentsView({webPreferences:{sandbox:true,nodeIntegration:false,contextIsolation:true,webSecurity:true,partition:'persist:nexsearch-services'}});
 guard(view.webContents);views.set(service,view);view.webContents.loadURL(services[service]).catch(()=>{});}
 active=view;win.contentView.addChildView(view);view.setBounds(bounds(rect));return true;}
async function quit(){if(exiting)return;exiting=true;hide();
 try{const response=await fetch(ui.origin+'/api/connection');const info=await response.json();await fetch(ui.origin+'/api/client/exit',{method:'POST',headers:{Origin:ui.origin,'X-Nexol-CSRF':info.csrf,'Content-Type':'application/json'},body:'{}',signal:AbortSignal.timeout(10000)});}catch{}
 for(const v of views.values())v.webContents.close();app.quit();}
app.whenReady().then(()=>{
 if(process.platform==='darwin')app.dock?.setIcon(path.join(__dirname,'logo.png'));
 for(const s of [session.defaultSession,session.fromPartition('persist:nexsearch-services')]){s.setPermissionRequestHandler((_wc,_permission,cb)=>cb(false));s.setPermissionCheckHandler(()=>false);}
 win=new BrowserWindow({width:1440,height:940,minWidth:1040,minHeight:700,title:'NexSearch 1.6.0',backgroundColor:'#f5f6fa',autoHideMenuBar:true,show:!process.argv.includes('--hidden'),icon:path.join(__dirname,'logo.png'),webPreferences:{preload:path.join(__dirname,'preload.cjs'),sandbox:true,nodeIntegration:false,contextIsolation:true,webSecurity:true}});
 guard(win.webContents,true);win.on('close',event=>{if(!exiting){event.preventDefault();quit();}});
 for(const [name,fn] of Object.entries({'service:show':show,'service:hide':hide,'service:bounds':v=>active?.setBounds(bounds(v)),'service:back':()=>{if(active?.webContents.navigationHistory.canGoBack())active.webContents.navigationHistory.goBack();},'service:reload':()=>active?.webContents.reload(),'update:prepare':async()=>{
  if(process.platform!=='win32'||!program||!home)throw Error('Используйте комплект и инструкцию обновления для этой платформы');
  const selected=await dialog.showOpenDialog(win,{title:'Подписанное обновление NexSearch',filters:[{name:'ZIP',extensions:['zip']}],properties:['openFile']});if(selected.canceled)return null;
  const helper=path.join(program,'NexSearch Maintenance.exe');const child=require('child_process');
  const result=await new Promise((resolve,reject)=>child.execFile(helper,['prepare','--archive',selected.filePaths[0],'--program',program,'--home',home,'--role',role],{windowsHide:true,encoding:'utf8',maxBuffer:1024*1024},(error,out)=>{try{const data=JSON.parse(out);data.error?reject(Error(data.error)):error?reject(error):resolve(data)}catch{reject(Error('Не удалось проверить обновление'))}}));
  preparedUpdate=result.state_file;return result;
 },'update:apply':async(state)=>{
  if(typeof state!=='string'||state!==preparedUpdate)throw Error('Сначала проверьте обновление');
  if(role==='HostWindows'){const info=await (await fetch(ui.origin+'/api/connection')).json();const response=await fetch(ui.origin+'/api/admin/backups',{method:'POST',headers:{Origin:ui.origin,'X-Nexol-CSRF':info.csrf,'Content-Type':'application/json'},body:'{}',signal:AbortSignal.timeout(240000)});if(!response.ok)throw Error('Перед обновлением не удалось проверить резервную копию базы');}

  const fs=require('fs');const runner=path.join(home,'updates','runner-'+Date.now()+'.exe');fs.copyFileSync(path.join(program,'NexSearch Maintenance.exe'),runner);
  const child=require('child_process').spawn(runner,['apply','--state',state],{windowsHide:true,detached:true,stdio:'ignore'});child.unref();await quit();return true;
 },'clipboard:write':text=>{if(typeof text!=='string'||text.length>10000)throw Error('Invalid clipboard text');clipboard.writeText(text);return true;},'application:quit':quit,'document:xlsx':async(bytes,name)=>{
  if(!(bytes instanceof Uint8Array)||bytes.byteLength>32*1024*1024||bytes[0]!==80||bytes[1]!==75||typeof name!=='string'||name.length>200)throw Error('Invalid Excel document');
  const choice=await dialog.showSaveDialog(win,{title:'Сохранить Excel',defaultPath:name.replace(/[\\/:*?"<>|]/g,'-'),filters:[{name:'Excel',extensions:['xlsx']}]});if(choice.canceled)return false;
  await require('fs').promises.writeFile(choice.filePath,bytes);return true;
 },'document:pdf':async(html,name)=>{
  if(typeof html!=='string'||html.length>8*1024*1024||typeof name!=='string'||name.length>200)throw Error('Invalid print document');
  const choice=await dialog.showSaveDialog(win,{title:'Сохранить схему погрузки',defaultPath:name.replace(/[\\/:*?"<>|]/g,'-')+'.pdf',filters:[{name:'PDF',extensions:['pdf']}]});if(choice.canceled)return false;
  const paper=new BrowserWindow({show:false,webPreferences:{sandbox:true,nodeIntegration:false,contextIsolation:true,webSecurity:true}});
  paper.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  const secure=html.replace(/<script[\s\S]*?<\/script>/gi,'').replace('<head>','<head><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; img-src data:">');
  try{await paper.loadURL('data:text/html;charset=utf-8,'+encodeURIComponent(secure));const buffer=await paper.webContents.printToPDF({printBackground:true,pageSize:'A4'});await require('fs').promises.writeFile(choice.filePath,buffer);return true;}finally{paper.destroy();}
 },'service:route':async(service,from,to)=>{
  if(!Object.hasOwn(services,service)||typeof from!=='string'||typeof to!=='string'||from.length>160||to.length>160)throw Error('Invalid route');const view=views.get(service);if(!view)return false;
  if(service==='yandex'){await view.webContents.loadURL(services.yandex+'?rtext='+encodeURIComponent(from+'~'+to));return true;}
  if(service==='calculator'){return await view.webContents.executeJavaScript(`(()=>{const values=${JSON.stringify({from,to})};let count=0;for(const key of ['from','to']){const el=document.querySelector('input[name="'+key+'"]');if(el){el.value=values[key];el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));count++;}}return count===2;})()`);}
  return false;
 }})){ipcMain.handle(name,(event,...args)=>{trusted(event);return fn(...args);});}
 win.loadURL(ui.href);
});
app.on('window-all-closed',()=>app.quit());
