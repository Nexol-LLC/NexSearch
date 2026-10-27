/* NexSearch 1.6: explicit workspace boundaries, company actions, mail and alerts. */
Object.assign(names,{mail:'Почта'});
Object.assign(actionNames,{'search.inn':'Поиск по ИНН','mail.connect':'Подключён почтовый ящик','mail.disconnect':'Отключён почтовый ящик','mail.send':'Отправлено письмо'});
const mailTab16=document.createElement('button');mailTab16.dataset.view='mail';mailTab16.innerHTML='<span>✉</span>Почта';
navRoot.insertBefore(mailTab16,document.querySelector('.management'));
mailTab16.onclick=()=>{workspaceOwner=null;teamPerson=null;readonly=false;show('mail')};
const mailSection16=document.createElement('section');mailSection16.id='mail-view16';mailSection16.hidden=true;document.querySelector('main').append(mailSection16);
let mailAccounts16=[],mailItems16=[],mailAccount16='',mailFolder16='INBOX',mailQuery16='',mailRequest16=0,companyCard16=null,notificationTimer16=null,editorRevision16=0,listRequest16=0;
const asyncError16=error=>{if(dialogOpen)$('#editor-error').textContent=error.message;else notice(error.message,true)};
const oldOpen16=openEditor;
openEditor=(...args)=>{++editorRevision16;companyCard16=null;$('#editor').classList.remove('company-dialog16','mail-compose16');oldOpen16(...args);$('#editor').scrollTop=0};
const previousClose16=closeEditor;closeEditor=()=>{++editorRevision16;companyCard16=null;previousClose16()};

// Empty the old view before waiting for another section's data. Only a current
// navigation ticket is allowed to install the resulting rows.
const priorShow16=show;
show=async next=>{
 window.scrollTo({top:0,left:0,behavior:'instant'});
 if(!['search','clients','carriers','pack','rates','maps','team','history','today','tasks','vat','tools','mail'].includes(next))next='today';
 mailSection16.hidden=next!=='mail';++mailRequest16;
 $('#kanban').hidden=true;$('#table-panel').hidden=next==='clients'&&board;
 $('#table-body').replaceChildren();$('#table-head').replaceChildren();$('#kanban').replaceChildren();
 $('#table-footer').textContent='';
 if(['search','clients'].includes(next))rows=[];
 if(next==='carriers')carriers=[];
 if(!['clients'].includes(next)){readonly=false;teamPerson=null}
 if(!['today','tasks'].includes(next))workspaceOwner=null;
 if(next!=='mail'){
  const promise=priorShow16(next),ticket=nav;
  try{await promise}catch(error){if(ticket===nav)notice(error.message,true)}
  if(ticket===nav&&view===next){drawInn16();if(next==='tasks')addTaskScope16()}
  return;
 }
 ++nav;view='mail';window.NexDesktop?.hide();document.body.classList.remove('service-layout15');
 for(const id of ['data-view','pack-view','external-view','team-view','history-view','job','search-controls','operations-view','preset-bar','fleet-filters15'])if($('#'+id))$('#'+id).hidden=true;
 $('#notice').hidden=true;$$('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view==='mail'));
 $('#page-title').textContent=$('#page-heading').textContent='Почта';$('#page-kicker').textContent='ПЕРЕПИСКА';$('#page-meta').textContent='Ваши почтовые ящики и письма';
 activity('view.mail');try{await loadMail16()}catch(error){notice(error.message,true)}
};
const priorLoad16=load;
load=async(ticket=nav)=>{
 if(ticket!==nav)return;
 if(view==='mail')return loadMail16();
 if(['search','clients','carriers'].includes(view)){
  const section=view,sequence=++listRequest16,owner=user?.id,person=readonly?teamPerson?.id:null;let data,stageValues,overviewData;
  if(section==='search')data=(await api('/api/leads')).companies;
  if(section==='carriers')data=(await api('/api/carriers')).carriers;
  if(section==='clients'){
   if(typeof clientOverview167==='function'&&clientOverview167()){
    overviewData=await api('/api/clients/overview?order='+encodeURIComponent($('#client-order').value)+clientOverviewQuery167());data=overviewData.companies;stageValues=overviewData.stages;
   }
   else if(person){data=(await api('/api/admin/employees/'+person+'/clients')).companies;stageValues=[...new Set(data.map(c=>c.stage||'В работе'))]}
   else {const result=await Promise.all([api('/api/clients?order='+encodeURIComponent($('#client-order').value)),api('/api/clients/stages')]);data=result[0].companies;stageValues=result[1].stages}
  }
  if(sequence!==listRequest16||ticket!==nav||view!==section||user?.id!==owner||person!==(readonly?teamPerson?.id:null))return;
  if(section==='carriers')carriers=data;else rows=data;
  if(stageValues){const selected=$('#stage-filter').value;stages=stageValues;$('#stage-filter').innerHTML='<option value="">Все этапы</option>'+stages.map(s=>`<option value="${esc(s)}">${esc(s||'В работе')}</option>`).join('');$('#stage-filter').value=selected}
  if(overviewData)drawClientOverview167(overviewData);
  render();return;
 }
 if(!['team','history','today','tasks','orders'].includes(view))return;
 await priorLoad16(ticket);if(ticket===nav&&view==='tasks')addTaskScope16();
};
const priorRender16=render;
render=()=>{
 if(!['search','clients','carriers'].includes(view))return;
 priorRender16();if(!['search','clients'].includes(view))return;
 for(const node of $$('#table-body tr[data-id],#kanban .kanban-card')){
  const c=rows.find(row=>row.id===node.dataset.id);if(!c)continue;
  const anchor=node.matches('tr')?node.querySelector('td:nth-child(2)'):node.querySelector('.meta');
  if(anchor&&!anchor.querySelector('.company-clock16'))anchor.insertAdjacentHTML('beforeend',clockHtml16(c));
 }
};
function clockHtml16(company){const value=company.company_time||{};return `<small class="company-clock16" data-offset="${value.utc_offset??''}" title="Время по региону регистрации"><span>${esc(value.label||'Часовой пояс не определён')}</span>${value.utc_offset==null?'':`<b>${localClock16(value.utc_offset)}</b>`}</small>`}
function localClock16(hours){return new Date(Date.now()+hours*3600000).toLocaleTimeString('ru-RU',{timeZone:'UTC',hour:'2-digit',minute:'2-digit'})}
setInterval(()=>$$('.company-clock16[data-offset]:not([data-offset=""]) b').forEach(b=>b.textContent=localClock16(+b.parentElement.dataset.offset)),30000);

const innBar16=document.createElement('div');innBar16.id='inn-search16';innBar16.hidden=true;innBar16.innerHTML='<div><strong>Найти конкретную компанию</strong><small>По ИНН организации · 10 цифр</small></div><label><span class="sr-only16">ИНН организации</span><input id="inn-value16" inputmode="numeric" maxlength="10" placeholder="Введите ИНН" autocomplete="off"></label><button type="button" id="inn-find16">Найти по ИНН</button><span id="inn-status16" role="status"></span>';
$('#data-view').prepend(innBar16);
function drawInn16(){innBar16.hidden=view!=='search'}
$('#inn-value16').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();$('#inn-find16').click()}});
$('#inn-find16').onclick=async()=>{
 const inn=val('#inn-value16').trim();if(!/^\d{10}$/.test(inn)){$('#inn-status16').textContent='Нужен ИНН организации из 10 цифр';return}
 const ticket=nav,owner=user?.id,button=$('#inn-find16');button.disabled=true;$('#inn-status16').textContent='Проверяем компанию…';
 try{const result=await api('/api/search/inn',{method:'POST',body:JSON.stringify({inn})});if(user?.id!==owner)return;$('#inn-status16').textContent=result.added?'Компания добавлена':'Компания уже в вашей базе';if(ticket===nav&&view==='search'){await load(ticket);if(ticket===nav)editCompany(result.company)}}
 catch(error){$('#inn-status16').textContent=error.status===422?'Проверьте ИНН и статус организации':error.message}finally{button.disabled=false}
};

const previousCompany16=editCompany;
editCompany=company=>{
 previousCompany16(company);if(!company)return;companyCard16=company;
 const body=$('#editor-body');$('#editor').classList.add('wide','company-dialog16');
 const operations=body.querySelector('.client-operations');
 if(operations){
  operations.classList.add('company-actions16');body.prepend(operations);
  const call=operations.querySelector('#client-new-call');if(call){call.textContent='☎ Звонок';call.classList.add('primary');operations.prepend(call)}
  $('#client-contacts').textContent='Доп. контакты';$('#client-calls').textContent='История звонков';
  operations.style.top=$('#editor .dialog-head').getBoundingClientRect().height+'px';
  if(!$('#lead-task15')&&user.role!=='logist'&&company.employee_id){const task=document.createElement('button');task.type='button';task.id='lead-task15';task.textContent='+ Задача';task.onclick=()=>editTask14({company_id:company.id,employee_id:company.employee_id,title:'Связаться: '+company.name});operations.append(task)}
 }
 let group='overview';const sections={};
 for(const [key,title] of [['overview','Сведения о компании'],['contacts','Контакты'],['work','Работа с компанией'],['finance','Финансовые сведения']]){const section=document.createElement('section');section.className='company-section165';section.dataset.companySection165=key;section.innerHTML=`<h3>${title}</h3>`;sections[key]=section}
 for(const node of [...body.children]){
  if(node===operations)continue;
  if(node.tagName==='H3'&&node.textContent.startsWith('Финансовые'))group='finance';
  if(node.tagName==='H3'&&node.textContent==='Работа с компанией')group='work';
  if(node.classList.contains('detail')&&['Этап','Описание звонка','Маршрут'].includes(node.firstElementChild?.textContent))group='work';
  if(node.tagName==='H3'){node.remove();continue}
  const label=node.classList.contains('detail')?node.firstElementChild?.textContent:'';
  const section=node.classList.contains('working-contacts151')||['Телефоны','Почта'].includes(label)?'contacts':group;
  sections[section].append(node);
 }
 const header=document.createElement('div');header.className='company-summary16';header.innerHTML=`<div><span class="eyebrow">${esc(company.region||'Регион не указан')}</span>${clockHtml16(company)}</div><div>${badge(company.lead_status)}${company.stage?` <span class="stage-pill">${esc(company.stage)}</span>`:''}</div>`;
 if(operations)operations.after(header);else body.prepend(header);
 for(const [key,section] of Object.entries(sections))if(key==='contacts'||section.children.length>1)body.append(section);
 const mail=document.createElement('section');mail.id='company-mail16';mail.className='company-section165';mail.innerHTML='<h3>Переписка</h3><p class="muted">Загружаем переписку…</p>';body.append(mail);loadCompanyMail16(company,mail);
 if($('#lead-task15'))$('#lead-task15').onclick=()=>editTask14({company_id:company.id,employee_id:company.employee_id||user.id,title:'Связаться: '+company.name,description:company.call_note||''});
 const emailRow=[...body.querySelectorAll('.detail')].find(n=>n.firstElementChild?.textContent==='Почта');
 if(emailRow&&company.emails?.length){const write=document.createElement('button');write.type='button';write.className='mail-write-company16';write.textContent='Написать';write.onclick=()=>composeMail16({to:company.emails});emailRow.lastElementChild.append(write)}
};
async function loadCompanyMail16(company,container){
 try{const data=await api('/api/mail/messages?company_id='+company.id);if(companyCard16?.id!==company.id||!container.isConnected)return;
  const query=(company.name||'').replace(/^(ООО|ОАО|ПАО|АО|ЗАО|ИП)\s*/i,'').replace(/[«»"]/g,'').trim();
  container.innerHTML=`<div class="panel-heading"><h3>Ваша переписка с компанией</h3><button type="button" id="company-compose16">Написать</button></div><p class="muted">Совпадение по адресам компании и контактных лиц. Письма из ящиков других сотрудников здесь не показываются.</p><div class="company-mail-find16"><input id="company-mail-query16" aria-label="Название компании или адрес почты" value="${esc(query)}" placeholder="Название или адрес в загруженной почте"><button type="button" id="company-mail-find16">Найти адрес в моей почте</button></div><div id="company-mail-results16">${data.messages.map(message=>mailItem16(message)).join('')||'<div class="empty">Писем с известными адресами компании пока нет. Подключите ящик в разделе «Почта» и синхронизируйте нужную папку.</div>'}</div>`;
  const bind=()=>container.querySelectorAll('[data-mail-id16]').forEach(button=>button.onclick=()=>openMailDialog16(button.dataset.mailId16,company));bind();
  $('#company-compose16').onclick=()=>composeMail16({to:company.emails||[]});
  $('#company-mail-find16').onclick=async()=>{const query=val('#company-mail-query16').trim();if(!query)return;const button=$('#company-mail-find16');button.disabled=true;try{const result=await api('/api/mail/messages?'+new URLSearchParams({q:query,folder:'INBOX'}));if(companyCard16?.id!==company.id||!container.isConnected)return;$('#company-mail-results16').innerHTML='<p class="muted">Совпадения в вашей загруженной входящей почте. Проверьте адрес перед сохранением в компанию.</p>'+ (result.messages.map(mailItem16).join('')||'<div class="empty">Совпадений нет</div>');bind()}catch(error){asyncError16(error)}finally{if(button.isConnected)button.disabled=false}};
 }catch(error){if(container.isConnected)container.textContent=error.message}
}

// All editable task assignees come from the host's permission check.
editTask14=async(task={})=>{
 const revision=editorRevision16,ticket=nav;
 try{
  const assignees=(await api('/api/tasks/assignees')).employees;
  const owner=task.employee_id||workspaceOwner||user.id,allowed=assignees.some(p=>p.id===owner);
  const companies=allowed?(await api('/api/tasks/companies?employee_id='+owner)).companies:[];
  if(revision!==editorRevision16||ticket!==nav)return;
  openEditor(task.id?'Задача':'Новая задача',`<label>Исполнитель<select id="task-owner16" ${task.id?'disabled':''}>${assignees.map(p=>`<option value="${p.id}" ${p.id===owner?'selected':''}>${esc(p.first_name+' '+p.last_name)}${p.id===user.id?' · я':''}</option>`).join('')}${!allowed?`<option selected value="${esc(owner)}">Просмотр задачи сотрудника</option>`:''}</select></label>${field('task-title','Следующее действие',task.title||'','text','required maxlength="300"')}<label>Компания<select id="task-company">${companyOptions(companies,task.company_id)}</select></label><div class="form-grid">${field('task-due','Когда',dateField(task.due_at||new Date(Date.now()+3600000)),'datetime-local','required')}<label>Приоритет<select id="task-priority">${options({normal:'Обычный',high:'Высокий'},task.priority||'normal')}</select></label></div><label>Статус<select id="task-status">${options({open:'Открыта',done:'Выполнена',cancelled:'Отменена'},task.status||'open')}</select></label><label>Описание<textarea id="task-description" maxlength="5000">${esc(task.description)}</textarea></label>`,allowed?()=>api('/api/tasks'+(task.id?'/'+task.id:''),{method:task.id?'PUT':'POST',body:JSON.stringify({employee_id:val('#task-owner16'),company_id:val('#task-company')||null,order_id:task.order_id||null,title:val('#task-title'),description:val('#task-description'),due_at:readDate('#task-due'),status:val('#task-status'),priority:val('#task-priority'),...(task.id?{revision:task.revision}:{})})}):null);
  let sequence=0;$('#task-owner16').onchange=async()=>{const ticket=++sequence,target=val('#task-owner16');$('#save-editor').disabled=true;try{const list=(await api('/api/tasks/companies?employee_id='+target)).companies;if(ticket===sequence&&$('#task-company'))$('#task-company').innerHTML=companyOptions(list,null)}catch(error){asyncError16(error)}finally{if(ticket===sequence)$('#save-editor').disabled=false}};
 }catch(error){asyncError16(error)}
};
function addTaskScope16(){const button=$('#task-new')||$('#today-new-task');if(button&&user.role!=='logist')button.hidden=false;if($('#task-scope16')||!$('#task-new')||user.role==='logist')return;
 const select=document.createElement('select');select.id='task-scope16';select.setAttribute('aria-label','Задачи сотрудника');$('#task-new').before(select);
 api('/api/tasks/assignees').then(data=>{if(!select.isConnected)return;select.innerHTML=data.employees.map(p=>`<option value="${p.id}">${esc(p.first_name+' '+p.last_name)}${p.id===user.id?' · я':''}</option>`).join('');select.value=workspaceOwner||user.id;select.onchange=()=>{workspaceOwner=select.value===user.id?null:select.value;readonly=!!workspaceOwner;++nav;load(nav).catch(asyncError16)}}).catch(asyncError16);
}

// A durable bell, independent of the currently visible page.
const bell16=document.createElement('button');bell16.id='notifications-bell16';bell16.type='button';bell16.setAttribute('aria-label','Уведомления');bell16.innerHTML='<svg aria-hidden="true" width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9Z"/><path d="M9 21h6"/></svg><span>Уведомления</span><b hidden>0</b>';
$('.topbar').insertBefore(bell16,$('.connection-state'));
const alerts16=document.createElement('dialog');alerts16.id='notifications-dialog16';alerts16.innerHTML='<div class="alerts-head16"><h2>Уведомления</h2><button type="button" id="alerts-close16" aria-label="Закрыть">×</button></div><div class="alerts-toolbar16"><button type="button" id="alerts-read16">Прочитать все</button><span id="alerts-count16"></span></div><div id="alerts-list16"></div>';
document.body.append(alerts16);let alertsData16=[],notificationRefreshTicket16=0;
$('#alerts-close16').onclick=()=>{alerts16.close();dialogOpen=$('#editor').open;if(['rates','maps','tools'].includes(view))mountService()};
alerts16.addEventListener('cancel',()=>{dialogOpen=$('#editor').open;setTimeout(()=>mountService(),0)});
bell16.onclick=async()=>{window.NexDesktop?.hide();dialogOpen=true;alerts16.showModal();try{await refreshNotifications16(true)}catch(error){$('#alerts-list16').textContent=error.message}};
$('#alerts-read16').onclick=async()=>{try{await api('/api/notifications/read-all',{method:'POST',body:'{}'});await refreshNotifications16(true)}catch(error){$('#alerts-count16').textContent=error.message}};
async function refreshNotifications16(draw=false){
 if(!user)return;const ticket=++notificationRefreshTicket16,owner=user.id,data=await api('/api/notifications');if(user?.id!==owner||ticket!==notificationRefreshTicket16)return;
 alertsData16=data.notifications;window.dispatchEvent(new CustomEvent('nex:notifications',{detail:{owner,data}}));const count=bell16.querySelector('b');count.textContent=data.unread>99?'99+':data.unread;count.hidden=!data.unread;bell16.setAttribute('aria-label','Уведомления: '+data.unread+' непрочитанных');
 if(!draw&&!alerts16.open)return;$('#alerts-count16').textContent=data.unread+' непрочитанных';$('#alerts-list16').innerHTML=alertsData16.map(n=>`<div class="alert-row164"><button type="button" class="alert-item16 ${n.read_at?'':'unread'}" data-alert16="${n.id}"><span>${n.kind.startsWith('mail.')?'✉':'✓'}</span><div><strong>${esc(n.title)}</strong><p>${esc(n.description)}</p><small>${dateFull(n.created_at)}</small></div></button><button type="button" class="alert-remove164" data-delete-alert164="${n.id}" aria-label="Удалить уведомление">×</button></div>`).join('')||'<div class="empty">Уведомлений пока нет</div>';
 $$('[data-delete-alert164]').forEach(button=>button.onclick=()=>deleteNotification164(button.dataset.deleteAlert164,button));
 $$('[data-alert16]').forEach(button=>button.onclick=async()=>{const item=alertsData16.find(n=>n.id===button.dataset.alert16);try{await api('/api/notifications/'+item.id+'/read',{method:'POST',body:'{}'});alerts16.close();dialogOpen=$('#editor').open;if(item.kind.startsWith('mail.'))await openMailDialog16(item.entity_id);else if(item.kind.startsWith('task.'))await editTask14(await api('/api/tasks/'+item.entity_id));else if(item.kind==='search.finished'){readonly=false;teamPerson=null;await show('search')}await refreshNotifications16()}catch(error){notice(error.message,true)}});
}
const enterBefore16=enter;enter=async person=>{await enterBefore16(person);clearInterval(notificationTimer16);if(user){refreshNotifications16().catch(()=>{});notificationTimer16=setInterval(()=>refreshNotifications16().catch(()=>{}),15000)}};
const signOutBefore16=signOut;signOut=()=>{++notificationRefreshTicket16;alertsData16=[];window.dispatchEvent(new Event('nex:signout'));++nav;++mailRequest16;++mailReadTicket16;rows=[];carriers=[];clearInterval(notificationTimer16);alerts16.close();bell16.querySelector('b').hidden=true;mailAccounts16=[];mailItems16=[];mailAccount16='';mailFolder16='INBOX';mailSection16.replaceChildren();signOutBefore16()};

function mailItem16(message){return `<button type="button" class="mail-item16 ${message.seen?'':'unread'}" data-mail-id16="${message.id}"><strong>${esc(message.subject||'Без темы')}</strong><span>${esc(message.sender)}</span><small>${message.sent_at?dateFull(message.sent_at):'Дата не указана'}</small></button>`}
async function loadMail16(){
 const ticket=++mailRequest16,owner=user?.id,data=await api('/api/mail/accounts');if(ticket!==mailRequest16||view!=='mail'||user?.id!==owner)return;mailAccounts16=data.accounts;
 if(!mailAccounts16.some(a=>a.id===mailAccount16))mailAccount16=mailAccounts16[0]?.id||'';
 const current=mailAccounts16.find(a=>a.id===mailAccount16);
 mailSection16.innerHTML=`<div class="mail-toolbar16"><strong>Моя почта</strong><span class="grow"></span><button type="button" id="mail-connect16">+ Подключить ящик</button>${current?'<button type="button" id="mail-compose16" class="primary">Написать</button>':''}</div>${!current?'<div class="mail-empty16"><span>✉</span><h2>Подключите свою почту</h2><p>Входящие, переписка с компаниями и уведомления о новых письмах будут здесь. Доступ к ящику есть только у владельца.</p><button type="button" id="mail-empty-connect16" class="primary">Подключить почтовый ящик</button></div>':`<div class="mail-layout16"><aside class="mail-sidebar16"><label>Почтовый ящик<select id="mail-account16">${mailAccounts16.map(a=>`<option value="${a.id}" ${a.id===current.id?'selected':''}>${esc(a.label||a.email)}</option>`).join('')}</select></label><div class="mail-folders16">${(current.folders||[]).map(f=>`<button type="button" data-folder16="${esc(f.id)}" class="${f.id===mailFolder16?'active':''}">${esc(f.id.toUpperCase()==='INBOX'?'Входящие':f.name)}</button>`).join('')}</div><button type="button" id="mail-sync16">↻ Обновить почту</button><small id="mail-sync-status16" role="status">${esc(current.last_error|| (current.last_sync_at?'Обновлено '+dateFull(current.last_sync_at):'Первое подключение — обновите почту'))}</small><button type="button" id="mail-disconnect16" class="link">Отключить ящик</button></aside><section class="mail-list16"><label class="mail-search16"><span class="sr-only16">Поиск в загруженной почте</span><input id="mail-search16" type="search" placeholder="Поиск в загруженных письмах" value="${esc(mailQuery16)}"></label><div id="mail-items16"></div><button type="button" id="mail-older16">Загрузить более ранние</button></section><section id="mail-reader16" class="mail-reader16"><div class="empty">Выберите письмо</div></section></div>`}`;
 $('#mail-connect16').onclick=connectMail16;if(!current){$('#mail-empty-connect16').onclick=connectMail16;return}
 $('#mail-compose16').onclick=()=>composeMail16();$('#mail-account16').onchange=()=>{mailAccount16=val('#mail-account16');mailFolder16='INBOX';mailQuery16='';loadMail16().catch(asyncError16)};
 $$('[data-folder16]').forEach(button=>button.onclick=async()=>{mailFolder16=button.dataset.folder16;await loadMail16();if(view==='mail')$('#mail-sync16')?.click()});
 $('#mail-sync16').onclick=async()=>{const button=$('#mail-sync16'),account=mailAccount16,folder=mailFolder16;button.disabled=true;$('#mail-sync-status16').textContent='Синхронизация…';try{await api('/api/mail/accounts/'+account+'/sync',{method:'POST',body:JSON.stringify({folder})});if(view==='mail'&&account===mailAccount16&&folder===mailFolder16){await loadMail16();await refreshNotifications16()}}catch(error){if(button.isConnected)$('#mail-sync-status16').textContent=error.message}finally{if(button.isConnected)button.disabled=false}};
 $('#mail-disconnect16').onclick=()=>openEditor('Отключить '+current.email,'<p>Локальный почтовый кеш и сохранённый пароль будут удалены с хоста. Письма у провайдера останутся.</p>',async()=>{await api('/api/mail/accounts/'+current.id,{method:'DELETE'});mailAccount16=''});
 let timer;$('#mail-search16').oninput=()=>{mailQuery16=val('#mail-search16');clearTimeout(timer);timer=setTimeout(()=>loadMailItems16().catch(asyncError16),250)};
 $('#mail-older16').onclick=()=>loadOlderMail16().catch(asyncError16);
 await loadMailItems16();
}
async function loadMailItems16(){
 const ticket=mailRequest16,account=mailAccount16,folder=mailFolder16,query=mailQuery16;
 const data=await api('/api/mail/messages?'+new URLSearchParams({account_id:account,folder,q:query}));
 if(view!=='mail'||ticket!==mailRequest16||account!==mailAccount16||folder!==mailFolder16||query!==mailQuery16||!$('#mail-items16'))return;
 mailItems16=data.messages;$('#mail-items16').innerHTML=mailItems16.map(mailItem16).join('')||'<div class="empty">Писем нет. Нажмите «Обновить почту», чтобы загрузить последние 100 писем папки.</div>';
 $$('#mail-items16 [data-mail-id16]').forEach(button=>button.onclick=()=>readMail16(button.dataset.mailId16));
}
function mailBody16(message){return `<div class="mail-message-head16"><h2>${esc(message.subject||'Без темы')}</h2><p><b>От:</b> ${esc(message.sender)}</p><p><b>Кому:</b> ${esc((message.recipients||[]).join(', '))}</p><small>${message.sent_at?dateFull(message.sent_at):''}</small><div class="mail-message-actions16"><button type="button" id="mail-reply16">Ответить</button><button type="button" id="mail-unread16">Не прочитано</button></div></div><pre class="mail-body-text16">${esc(message.body_text||'Текст письма отсутствует')}</pre>${message.attachments?.length?'<h3>Вложения</h3>'+message.attachments.map(a=>`<button type="button" class="mail-attachment16" data-attachment16="${a.index}">${esc(a.name)} · ${Math.ceil(a.size/1024)} КБ ↓</button>`).join(''):''}<details class="mail-addresses16"><summary>Адреса из письма</summary>${(message.addresses||[]).map(a=>copyButton(a)).join(' ')}</details>`}
async function readMail16(ident){const ticket=++mailReadTicket16,request=mailRequest16;$('#mail-reader16').innerHTML='<div class="empty">Загружаем письмо…</div>';try{const message=await api('/api/mail/messages/'+ident);if(view!=='mail'||ticket!==mailReadTicket16||request!==mailRequest16)return;$('#mail-reader16').innerHTML=mailBody16(message);bindMailBody16(message,$('#mail-reader16'));$('#mail-items16 [data-mail-id16="'+ident+'"]')?.classList.remove('unread');refreshNotifications16().catch(()=>{})}catch(error){if(ticket===mailReadTicket16&&$('#mail-reader16'))$('#mail-reader16').textContent=error.message}}
let mailReadTicket16=0;
async function openMailDialog16(ident,company=null){const revision=editorRevision16,ticket=nav;try{const message=await api('/api/mail/messages/'+ident);if(revision!==editorRevision16||ticket!==nav)return;openEditor(message.subject||'Письмо',mailBody16(message),null);$('#editor').classList.add('wide');bindMailBody16(message,$('#editor-body'));
 if(company&&company.employee_id===user.id){const shown=editorRevision16,accounts=(await api('/api/mail/accounts')).accounts;if(shown!==editorRevision16||!$('#editor-body .mail-addresses16'))return;const own=new Set(accounts.map(a=>a.email.toLowerCase())),addresses=message.addresses.filter(a=>!own.has(a.toLowerCase())&&!(company.emails||[]).includes(a));
  const panel=document.createElement('section');panel.className='mail-found-addresses16';panel.innerHTML='<h3>Добавить рабочий адрес компании</h3>'+addresses.map(address=>`<button type="button" data-add-company-email16="${esc(address)}">+ ${esc(address)}</button>`).join('');$('#editor-body').append(panel);
  panel.querySelectorAll('button').forEach(button=>button.onclick=async()=>{button.disabled=true;try{const result=await api('/api/leads/'+company.id+'/contacts',{method:'PATCH',body:JSON.stringify({phones:company.phones||[],emails:[...new Set([...(company.emails||[]),button.dataset.addCompanyEmail16])],revision:company.contacts_revision||0})});Object.assign(company,result);const row=rows.find(c=>c.id===company.id);if(row)Object.assign(row,result);button.textContent='Адрес добавлен'}catch(error){button.disabled=false;asyncError16(error)}});
 }
 }catch(error){asyncError16(error)}}
function bindMailBody16(message,container){
 container.querySelector('#mail-reply16').onclick=()=>{const from=message.sender.match(/<([^>]+)>/)?.[1]||message.sender;composeMail16({account_id:message.account_id,to:[from],subject:/^re:/i.test(message.subject)?message.subject:'Re: '+message.subject,reply_to:message.id,text:'\n\n'+(message.body_text||'').split('\n').map(line=>'> '+line).join('\n')})};
 container.querySelector('#mail-unread16').onclick=async()=>{try{await api('/api/mail/messages/'+message.id,{method:'PATCH',body:JSON.stringify({seen:false})});if(dialogOpen)closeEditor();if(view==='mail')await loadMailItems16()}catch(error){asyncError16(error)}};
 container.querySelectorAll('[data-attachment16]').forEach(button=>button.onclick=async()=>{try{const response=await fetch('/api/mail/messages/'+message.id+'/attachments/'+button.dataset.attachment16);if(!response.ok)throw Error('Вложение недоступно');const object=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=object;link.download=message.attachments.find(a=>a.index===+button.dataset.attachment16)?.name||'attachment';link.click();setTimeout(()=>URL.revokeObjectURL(object),5000)}catch(error){asyncError16(error)}});
}
async function connectMail16(){
 openEditor('Подключить почту',`<p class="muted">Используйте пароль приложения для почтового клиента. Он хранится на хосте в зашифрованном виде; другие сотрудники не получают доступ к вашему ящику.</p><label>Провайдер<select id="mail-provider16"><option value="yandex">Яндекс</option><option value="custom">Другой IMAP сервер</option></select></label><div class="form-grid">${field('mail-email16','Ваш адрес','','email','required autocomplete="email"')}${field('mail-login16','Логин почты','','text','required autocomplete="username"')}</div>${field('mail-password16','Пароль приложения','','password','required autocomplete="new-password"')}<div class="form-grid">${field('mail-imap16','IMAP сервер','imap.yandex.ru','text','required')}${field('mail-smtp16','SMTP сервер','smtp.yandex.ru')}</div><p class="muted">Защищённое соединение: IMAP 993, SMTP 465. Для получения писем достаточно IMAP; SMTP нужен для отправки.</p><a href="https://yandex.ru/support/yandex-360/customers/mail/ru/mail-clients/others" target="_blank" rel="noreferrer">Как разрешить IMAP и создать пароль приложения в Яндексе ↗</a><p id="mail-connect-state16" role="status"></p>`,async()=>{
  $('#mail-connect-state16').textContent='Проверяем подключение…';const body={email:val('#mail-email16'),login:val('#mail-login16'),password:val('#mail-password16'),imap_host:val('#mail-imap16'),smtp_host:val('#mail-smtp16'),imap_port:993,smtp_port:465};
  const data=await api('/api/mail/accounts',{method:'POST',body:JSON.stringify(body)});$('#mail-password16').value='';mailAccount16=data.id;mailFolder16='INBOX';try{await api('/api/mail/accounts/'+data.id+'/sync',{method:'POST',body:JSON.stringify({folder:'INBOX'})})}catch(error){notice('Ящик подключён. '+error.message,true)}
 });$('#save-editor').textContent='Подключить';$('#mail-email16').oninput=()=>$('#mail-login16').value=val('#mail-email16');$('#mail-provider16').onchange=()=>{if(val('#mail-provider16')==='yandex'){$('#mail-imap16').value='imap.yandex.ru';$('#mail-smtp16').value='smtp.yandex.ru'}else{$('#mail-imap16').value='';$('#mail-smtp16').value=''}};
}
async function composeMail16(draft={}){
 const revision=editorRevision16,ticket=nav;
 try{const accounts=(await api('/api/mail/accounts')).accounts;if(revision!==editorRevision16||ticket!==nav)return;if(!accounts.length){await connectMail16();return}
  openEditor('Новое письмо',`<label>От кого<select id="compose-account16">${accounts.map(a=>`<option value="${a.id}" ${a.id===(draft.account_id||mailAccount16)?'selected':''}>${esc(a.email)}</option>`).join('')}</select></label>${field('compose-to16','Кому',(draft.to||[]).join(', '),'text','required placeholder="Адреса через запятую"')}${field('compose-cc16','Копия','','text')}${field('compose-subject16','Тема',draft.subject||'','text','required maxlength="500"')}<label>Письмо<textarea id="compose-text16" rows="14" maxlength="200000" required>${esc(draft.text||'')}</textarea></label><p id="mail-send-state16" role="status"></p>`,async()=>{const split=id=>val(id).split(/[,;\n]/).map(s=>s.trim()).filter(Boolean);$('#mail-send-state16').textContent='Отправляем…';const result=await api('/api/mail/send',{method:'POST',body:JSON.stringify({account_id:val('#compose-account16'),to:split('#compose-to16'),cc:split('#compose-cc16'),subject:val('#compose-subject16'),text:val('#compose-text16'),reply_to:draft.reply_to||null})});notice(result.warning||'Письмо отправлено')});$('#editor').classList.add('wide','mail-compose16');$('#save-editor').textContent='Отправить';
 }catch(error){asyncError16(error)}
}

async function loadOlderMail16(){
 const button=$('#mail-older16'),account=mailAccount16,folder=mailFolder16,ticket=mailRequest16;
 const oldest=mailItems16.length?Math.min(...mailItems16.map(m=>m.uid)):null;
 if(!oldest)return;
 button.disabled=true;button.textContent='Загружаем…';
 try{
  const query=new URLSearchParams({account_id:account,folder,q:mailQuery16,before_uid:oldest});
  let result=await api('/api/mail/messages?'+query);
  if(!result.messages.length&&!mailQuery16){
   await api('/api/mail/accounts/'+account+'/sync',{method:'POST',body:JSON.stringify({folder,before_uid:oldest})});
   result=await api('/api/mail/messages?'+query);
  }
  if(view!=='mail'||ticket!==mailRequest16||account!==mailAccount16||folder!==mailFolder16)return;
  const known=new Set(mailItems16.map(m=>m.id));mailItems16.push(...result.messages.filter(m=>!known.has(m.id)));
  $('#mail-items16').innerHTML=mailItems16.map(mailItem16).join('');
  $$('#mail-items16 [data-mail-id16]').forEach(b=>b.onclick=()=>readMail16(b.dataset.mailId16));
  button.textContent=result.messages.length?'Загрузить более ранние':'Более ранних писем нет';button.disabled=!result.messages.length;
 }catch(error){if(button.isConnected){button.disabled=false;button.textContent='Повторить загрузку'}throw error}
}
