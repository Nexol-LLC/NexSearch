/* Employees can inspect connectivity; host credentials and settings stay private. */
const connectionSettings=document.createElement('button');connectionSettings.textContent='Настройки';document.querySelector('.session-actions').append(connectionSettings);
let hostSettingsTimer=null,settingsTab='host',settingsEpoch=0;
function settingsVisible(tab,epoch){return dialogOpen&&$('#host-settings-content')&&settingsTab===tab&&settingsEpoch===epoch}
function selectSettingsTab(tab){
 clearInterval(hostSettingsTimer);settingsTab=tab;settingsEpoch++;
 $('#settings-host-tab').classList.toggle('primary',tab==='host');$('#settings-update-tab').classList.toggle('primary',tab==='updates');
 const epoch=settingsEpoch,load=tab==='host'?loadHostSettings:loadLocalUpdates;
 if(tab==='updates')$('#host-settings-content').innerHTML='<div id="update-details" aria-live="polite">Проверяем состояние…</div><button type="button" id="check-update-now">Проверить сейчас</button><p id="update-request-message" role="status"></p>';
 load();hostSettingsTimer=setInterval(()=>{if(!settingsVisible(tab,epoch)){clearInterval(hostSettingsTimer);return}load()},tab==='host'?5000:1500);
}
connectionSettings.onclick=()=>openHostSettings();
async function openHostSettings(){
 openEditor('Настройки',`<div class="order-actions"><button type="button" class="primary" id="settings-host-tab">Хост</button><button type="button" id="settings-update-tab">Обновления</button></div><div id="host-settings-content">Проверяем подключение…</div>`,null);
 $('#settings-host-tab').onclick=()=>selectSettingsTab('host');
 $('#settings-update-tab').onclick=()=>selectSettingsTab('updates');
 selectSettingsTab('host');
}
async function loadHostSettings(){const epoch=settingsEpoch;if(!settingsVisible('host',epoch))return;try{const [host,connection]=await Promise.all([api('/api/host/status'),fetch('/api/connection').then(r=>r.json())]);if(!settingsVisible('host',epoch))return;const address=connection.host_url||connection.host||host.ip;$('#host-settings-content').innerHTML=`<div class="panel"><span class="badge taken">Хост доступен</span><h3>${esc(host.hostname)}</h3>${detail('IP / адрес',address)}${detail('Версия',host.version)}${detail('CPU',host.cpu_percent.toFixed(1)+'%')}${detail('Оперативная память',(host.memory_used_bytes/1024**3).toFixed(1)+' / '+(host.memory_total_bytes/1024**3).toFixed(1)+' ГБ · '+host.memory_percent+'%')}${detail('Свободно на диске',(host.disk_free_bytes/1024**3).toFixed(1)+' ГБ')}${detail('Поиск',host.queue.running+' задач выполняется · '+host.queue.queued+' в очереди')}${detail('Проверка обновлений','Ежедневно в 10:00 МСК')}${detail('Последняя проверка',host.updates.checked_at?dateFull(host.updates.checked_at):'Ещё не выполнялась')}</div>`}catch(e){if(!settingsVisible('host',epoch))return;$('#host-settings-content').innerHTML='<span class="badge refused">Хост недоступен</span><p>'+esc(e.message)+'</p>'}}
let updateRequestBusy=false,updateRestartIntent=null;
async function loadLocalUpdates(){
 const epoch=settingsEpoch;
 if(!settingsVisible('updates',epoch))return;
 try{
  if(location.protocol==='https:'){
   const host=await api('/api/host/status');if(!settingsVisible('updates',epoch))return;
   $('#update-details').innerHTML=`<p>Хост получает обновления GitHub ежедневно в 10:00 МСК. Программа сотрудника скачивает свой пакет с хоста.</p>${detail('Состояние',host.updates.message||'Ожидаем проверку')}${detail('Проверено',host.updates.checked_at?dateFull(host.updates.checked_at):'—')}${detail('Версия сервера',host.version)}`;$('#check-update-now').hidden=true;return;
  }
  const state=await api('/api/client/update/status');if(!settingsVisible('updates',epoch))return;
  const ready=state.phase==='ready'||(state.pending_state&&state.phase!=='error');
  const message=ready?(state.supports_restart?'Обновление готово. Нажмите «Установить и перезапустить».':'Обновление готово. Полностью закройте программу и откройте её после установки.'):state.message||'Проверка ещё не выполнялась';
  const progress=state.phase==='downloading'&&state.total_bytes?detail('Скачано',(state.downloaded_bytes/1024**2).toFixed(1)+' / '+(state.total_bytes/1024**2).toFixed(1)+' МБ'):'';
  $('#update-details').innerHTML=`<p>Пакет сотрудника: ветка employe, через хост. Проверка ежедневно в 10:00 МСК.</p>${detail('Установлена у сотрудника',state.installed_version||document.querySelector('.brand small')?.textContent||'—')}${detail('Пакет на хосте',state.version||'—')}${detail('Состояние',message)}${progress}${detail('Проверка сотрудника',state.checked_at?dateFull(state.checked_at):'—')}${detail('Проверка GitHub хостом',state.host_checked_at?dateFull(state.host_checked_at):'—')}${state.last_error?detail('Код ошибки',state.last_error):''}`;
  const button=$('#check-update-now');button.disabled=updateRequestBusy||!!state.checking;
  button.textContent=state.supports_restart?(ready?'Установить и перезапустить':'Обновить и перезапустить'):'Проверить сейчас';
  if(updateRestartIntent&&state.supports_restart&&ready&&!state.checking&&!updateRequestBusy){
   updateRequestBusy=true;button.disabled=true;updateRestartIntent=null;
   $('#update-request-message').textContent='Устанавливаем обновление. Программа откроется автоматически…';
   try{
    await api('/api/client/update/install',{method:'POST',body:'{}'});
    if(window.NexDesktop?.quit)await window.NexDesktop.quit();
    else{await api('/api/client/exit',{method:'POST',body:'{}'});$('#update-request-message').textContent='Обновление устанавливается. Программа откроется автоматически.'}
   }catch(e){updateRequestBusy=false;if(settingsVisible('updates',epoch))$('#update-request-message').textContent=e.message}
   return;
  }
  if(updateRestartIntent&&!state.checking&&(['error','waiting'].includes(state.phase)||state.phase==='current'&&state.checked_at!==updateRestartIntent.checked_at))updateRestartIntent=null;
  button.onclick=async()=>{
   if(state.supports_restart&&ready){updateRestartIntent={checked_at:state.checked_at};return loadLocalUpdates()}
   updateRequestBusy=true;button.disabled=true;$('#update-request-message').textContent='Запускаем проверку…';
   try{
    const result=await api('/api/client/update/check',{method:'POST',body:'{}'});
    if(result.queued&&state.supports_restart)updateRestartIntent={checked_at:state.checked_at};
    if(settingsVisible('updates',epoch))$('#update-request-message').textContent=result.queued?(state.supports_restart?'Скачиваем и проверяем обновление. После установки программа откроется автоматически.':'Проверка запущена. Состояние обновляется автоматически.'):'Обновлятель сотрудника недоступен в этом режиме. Откройте программу сотрудника.';
   }catch(e){if(settingsVisible('updates',epoch))$('#update-request-message').textContent=e.message}
   finally{updateRequestBusy=false;if(settingsVisible('updates',epoch))loadLocalUpdates()}
  };
 }catch(e){if(settingsVisible('updates',epoch))$('#update-details').textContent=e.message}
}
