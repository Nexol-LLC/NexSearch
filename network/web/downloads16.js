/* One download path, a native save dialog and visible failures in every view. */
async function downloadFile16(response,name,excel=false){
 if(!response.ok){let detail='';try{const body=await response.json();detail=body.error||''}catch{}throw Error(detail||'Хост не подготовил файл ('+response.status+')')}
 const bytes=new Uint8Array(await response.arrayBuffer());
 if(excel&&(bytes[0]!==80||bytes[1]!==75))throw Error('Хост вернул ответ в неверном формате Excel');
 if(excel&&window.NexDesktop?.exportXlsx)return window.NexDesktop.exportXlsx(bytes,name);
 if(!excel&&window.NexDesktop?.saveFile)return window.NexDesktop.saveFile(bytes,name);
 const object=URL.createObjectURL(new Blob([bytes],{type:response.headers.get('content-type')||'application/octet-stream'}));
 const link=document.createElement('a');link.href=object;link.download=name;link.hidden=true;document.body.append(link);link.click();setTimeout(()=>{link.remove();URL.revokeObjectURL(object)},60000);return true;
}
window.NexDownload16=downloadFile16;
document.addEventListener('click',async event=>{
 const link=event.target.closest('a[href^="/api/export"]');if(!link)return;event.preventDefault();event.stopImmediatePropagation();
 if(link.dataset.downloading16)return;
 const href=link.getAttribute('href'),section={search:'компании',clients:'клиенты',carriers:'перевозчики'}[view]||'выгрузка';
 link.dataset.downloading16='true';link.setAttribute('aria-busy','true');
 try{const saved=await downloadFile16(await fetch(href),'NexSearch-'+section+'.xlsx',true);notice(saved?'Excel сохранён':'Сохранение отменено')}
 catch(error){notice('Не удалось сохранить Excel: '+error.message,true)}finally{delete link.dataset.downloading16;link.removeAttribute('aria-busy')}
},true);
