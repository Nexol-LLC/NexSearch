/* Private popups and recoverable PackOnTruck startup. */
'use strict';
const toastStack164=document.createElement('div');toastStack164.id='notification-popups164';toastStack164.setAttribute('aria-live','polite');toastStack164.setAttribute('aria-label','Новые уведомления');document.body.append(toastStack164);
if(typeof toastStack164.showPopover==='function')toastStack164.setAttribute('popover','manual');
function mountPopups164(){
 const target=[...document.querySelectorAll('dialog[open]')].at(-1)||document.body;
 if(toastStack164.parentElement!==target){toastStack164.hidePopover?.();target.append(toastStack164)}
 if(toastStack164.children.length&&toastStack164.showPopover&&!toastStack164.matches(':popover-open'))toastStack164.showPopover();
}
new MutationObserver(mountPopups164).observe(document.body,{subtree:true,attributes:true,attributeFilter:['open']});
let popupOwner164=null,popupSeen164=new Set();
const popupTimers164=new Map();
function forgetPopup164(id){clearTimeout(popupTimers164.get(id));popupTimers164.delete(id);document.querySelector('[data-popup164="'+id+'"]')?.remove();if(!toastStack164.children.length)toastStack164.hidePopover?.()}
function clearPopups164(){for(const id of popupTimers164.keys())forgetPopup164(id);toastStack164.replaceChildren();window.NexDesktop?.clearNotifications?.().catch(()=>{})}
async function openNotification164(item,owner=user?.id){
 if(!item||!user||user.id!==owner)return;
 forgetPopup164(item.id);
 try{
  await api('/api/notifications/'+item.id+'/read',{method:'POST',body:'{}'});
  if(user?.id!==owner)return;
  alerts16.close();dialogOpen=$('#editor').open;await window.NexDesktop?.hide();
  if(item.kind.startsWith('mail.'))await openMailDialog16(item.entity_id);
  else if(item.kind.startsWith('task.'))await editTask14(await api('/api/tasks/'+item.entity_id));
  else if(item.kind==='client.transferred')await openCompany162(item.entity_id);
  else if(item.kind==='search.finished'){readonly=false;teamPerson=null;await show('search')}
  await refreshNotifications16();
 }catch(e){notice(e.message,true)}
}
function popup164(item,owner){
 const card=document.createElement('article');card.className='notification-popup164';card.dataset.popup164=item.id;
 card.innerHTML=`<button type="button" class="popup-open164"><small>${item.kind==='task.overdue'?'Просроченная задача':item.kind.startsWith('mail.')?'Новое письмо':'Уведомление'}</small><strong>${esc(item.title)}</strong><p>${esc(item.description)}</p><time>${dateFull(item.created_at)}</time></button><button type="button" class="popup-close164" aria-label="Закрыть уведомление">×</button>`;
 card.querySelector('.popup-open164').onclick=()=>openNotification164(item,owner);
 card.querySelector('.popup-close164').onclick=()=>forgetPopup164(item.id);
 toastStack164.prepend(card);
 mountPopups164();
 if(toastStack164.showPopover){toastStack164.hidePopover();toastStack164.showPopover()}
 const schedule=()=>popupTimers164.set(item.id,setTimeout(()=>forgetPopup164(item.id),12000));schedule();
 card.onmouseenter=card.onfocusin=()=>clearTimeout(popupTimers164.get(item.id));card.onmouseleave=card.onfocusout=schedule;
 while(toastStack164.children.length>3)forgetPopup164(toastStack164.lastElementChild.dataset.popup164);
 window.NexDesktop?.notify?.({id:item.id,owner,title:item.title,body:item.description}).catch(()=>{});
}
window.addEventListener('nex:notifications',event=>{
 const {owner,data}=event.detail;if(!user||user.id!==owner)return;
 let baseline=false;
 if(popupOwner164!==owner){clearPopups164();popupOwner164=owner;baseline=true;try{popupSeen164=new Set(JSON.parse(localStorage.getItem('nex.notifications.seen.'+owner)||'[]'))}catch{popupSeen164=new Set()}}
 const fresh=data.notifications.filter(n=>!popupSeen164.has(n.id)&&!n.read_at&&(!baseline||Date.now()-Date.parse(n.created_at)<120000));
 for(const n of data.notifications){popupSeen164.add(n.id);if(n.read_at)forgetPopup164(n.id)}
 popupSeen164=new Set([...popupSeen164].slice(-1000));
 try{localStorage.setItem('nex.notifications.seen.'+owner,JSON.stringify([...popupSeen164]))}catch{}
 fresh.slice(0,3).reverse().forEach(n=>popup164(n,owner));
 const visible=new Set(data.notifications.map(n=>n.id));for(const id of popupTimers164.keys())if(!visible.has(id))forgetPopup164(id);
});
window.addEventListener('nex:signout',()=>{clearPopups164();popupOwner164=null;popupSeen164.clear()});
window.NexDesktop?.onNotification?.(({id,owner})=>{if(user?.id===owner)openNotification164(alertsData16.find(n=>n.id===id),owner)});
const clearAlerts164=document.createElement('button');clearAlerts164.type='button';clearAlerts164.id='alerts-clear164';clearAlerts164.textContent='Очистить историю';$('#alerts-read16').after(clearAlerts164);
clearAlerts164.onclick=async()=>{
 const owner=user?.id;clearAlerts164.disabled=true;++notificationRefreshTicket16;
 try{await api('/api/notifications/clear',{method:'POST',body:'{}'});if(user?.id!==owner)return;clearPopups164();await refreshNotifications16(true)}catch(e){$('#alerts-count16').textContent=e.message}finally{clearAlerts164.disabled=false}
};
async function deleteNotification164(id,button){
 const owner=user?.id;button.disabled=true;++notificationRefreshTicket16;
 try{await api('/api/notifications/'+id,{method:'DELETE'});if(user?.id!==owner)return;forgetPopup164(id);await refreshNotifications16(true)}catch(e){$('#alerts-count16').textContent=e.message;button.disabled=false}
}
Object.assign(actionNames,{'notifications.clear':'Очищена история уведомлений'});
(()=>{
 const frame=$('#pack-frame'),section=$('#pack-view'),status=document.createElement('div');
 status.id='pack-status164';status.hidden=true;status.innerHTML='<div><strong id="pack-status-title164">Загружаем погрузку…</strong><p id="pack-status-text164">Подготовка схемы и каталога транспорта</p><button type="button" id="pack-retry164" hidden>Повторить загрузку</button></div>';section.append(status);
 let phase='idle',timer=null,attempts=0,healthy=false;
 function showStatus(title,text,retry=false){status.hidden=false;$('#pack-status-title164').textContent=title;$('#pack-status-text164').textContent=text;$('#pack-retry164').hidden=!retry}
 function ready(){clearTimeout(timer);phase='ready';healthy=true;attempts=0;status.hidden=true;frame.contentWindow.dispatchEvent(new Event('resize'))}
 function fail(){
  clearTimeout(timer);phase='error';showStatus('Погрузка не загрузилась','Проверьте соединение и повторите загрузку.',true);
  if(!healthy&&attempts<2&&view==='pack')start();
 }
 function start(){
  clearTimeout(timer);healthy=false;phase='loading';attempts++;showStatus('Загружаем погрузку…','Подготовка схемы и каталога транспорта');
  frame.src='/pack/?v=164&attempt='+Date.now();timer=setTimeout(fail,25000);
 }
 frame.addEventListener('load',()=>{
  if(phase!=='loading')return;
  try{if(frame.contentWindow.NexPackReady)ready();else if(frame.contentWindow.NexPackFailed||!frame.contentDocument.querySelector('script[src="src/readiness164.js"]'))fail()}catch{fail()}
 });
 frame.addEventListener('error',fail);
 window.addEventListener('message',event=>{
  if(event.source!==frame.contentWindow||event.origin!==location.origin||event.data?.type!=='nex:pack-status'||event.data.instance!==new URL(frame.src).search)return;
  if(event.data.status==='ready')ready();else if(event.data.status==='failed')fail();
  else if(event.data.status==='context-lost'){clearTimeout(timer);phase='context-lost';showStatus('Восстанавливаем схему','Графический контекст временно недоступен. Данные погрузки сохранены в открытой вкладке.')}
 });
 $('#pack-retry164').onclick=()=>{attempts=0;start()};
 window.NexPack=Object.freeze({open(){
  if(phase==='idle'||phase==='error'&&!healthy){attempts=0;start()}
  else if(phase==='ready'){status.hidden=true;requestAnimationFrame(()=>frame.contentWindow.dispatchEvent(new Event('resize')))}
 }});
 if(view==='pack')window.NexPack.open();
})();
