/* The relay is provisioned by the administrator; employees never enter secrets. */
const internetOption166='<option value="internet">Через интернет</option>';
for(const select of $$('[data-transport16]'))select.querySelector('[value="vpn"]').insertAdjacentHTML('beforebegin',internetOption166);
const controlBefore166=transportControl16;transportControl16=()=>{const label=controlBefore166();label.querySelector('[value="vpn"]').insertAdjacentHTML('beforebegin',internetOption166);return label};
function internetMessage166(s){if(s.internet_error)return s.internet_error;if(!s.internet_configured)return 'Для подключения из дома администратор должен установить файл подключения к шлюзу.';return s.internet_online?'Офисный сервер доступен через интернет.':'Проверяем соединение с офисом. Если сервер или шлюз выключен, подключение восстановится автоматически.'}
const paintBefore166=paintTransport16;paintTransport16=s=>{
 paintBefore166(s);
 const hint=$('#transport-help16');if(hint)hint.textContent=s.preference==='internet'?'Только интернет-шлюз. Локальная сеть не используется.':s.preference==='auto'?'В офисе — локальная сеть, вне офиса — интернет-шлюз при наличии файла подключения.':hint.textContent;
 if(s.preference!=='vpn'){$('#vpn-auth-enable16').hidden=$('#vpn-auth-custom16').hidden=$('#vpn-auth-login16').hidden=true;$('#vpn-auth-status16').textContent=internetMessage166(s)}
 if(user&&s.preference==='internet'&&!s.host_found)$('#host-state').textContent=s.internet_configured?'Ожидаем интернет-подключение':'Шлюз ещё не настроен';
};
const labelBefore166=window.nexConnectionLabel16;window.nexConnectionLabel16=()=>transportState16?.transport==='internet'?'Хост подключён · Интернет':transportState16?.preference==='internet'?'Ожидаем интернет-подключение':labelBefore166();
const authBefore166=refreshVpnAuth16;refreshVpnAuth16=async()=>{await authBefore166();if(transportState16)paintTransport16(transportState16)};
const hostSettingsBefore166=openHostSettings;openHostSettings=async()=>{await hostSettingsBefore166();if(!$('#host-settings-content'))return;const button=document.createElement('button');button.id='settings-internet166';button.type='button';button.textContent='Интернет';$('#settings-host-tab').after(button);button.onclick=showInternetSettings166};
async function showInternetSettings166(){
 clearInterval(hostSettingsTimer);settingsEpoch++;settingsTab='internet';const epoch=settingsEpoch;
 for(const b of $$('.settings-nav162 button'))b.classList.toggle('primary',b.id==='settings-internet166');
 const area=$('#host-settings-content');area.innerHTML='<section class="internet-panel166"><h3>Офисный сервер через интернет</h3><p>Программа подключается к офисной базе без установки VPN. Файл подключения подготавливает администратор.</p><div class="internet-state166" id="internet-state166" role="status">Проверяем подключение…</div><dl><dt>Шлюз</dt><dd id="internet-url166">—</dd><dt>Последняя проверка</dt><dd id="internet-check166">—</dd></dl><div class="internet-buttons166"><button type="button" id="internet-recheck166">Проверить соединение</button><button type="button" id="internet-use166">Использовать интернет</button><button type="button" id="internet-auto166">Автоматический выбор</button></div><p class="muted">При обрыве сети программа восстанавливает подключение. Если сохранение не подтвердилось, проверьте данные перед повторной отправкой.</p></section>';
 const current=()=>dialogOpen&&settingsTab==='internet'&&settingsEpoch===epoch&&area.isConnected;
 const draw=async()=>{try{const s=await api('/api/client/transport');if(!current())return;$('#internet-state166').textContent=internetMessage166(s);$('#internet-state166').dataset.online=String(s.internet_online);$('#internet-url166').textContent=s.internet_url||'Файл подключения не установлен';$('#internet-check166').textContent=s.internet_checked_at?dateFull(s.internet_checked_at):'Ещё не выполнялась';$('#internet-use166').disabled=!s.internet_configured;paintTransport16(s)}catch(e){if(current())$('#internet-state166').textContent=e.message}};
 $('#internet-recheck166').onclick=async()=>{try{await api('/api/client/internet/check',{method:'POST',body:'{}'});await draw()}catch(e){if(current())$('#internet-state166').textContent=e.message}};
 $('#internet-use166').onclick=()=>changeTransport16('internet');$('#internet-auto166').onclick=()=>changeTransport16('auto');
 await draw();hostSettingsTimer=setInterval(()=>{if(current())draw();else clearInterval(hostSettingsTimer)},2000);
}
const selectSettingsBefore166=selectSettingsTab;selectSettingsTab=tab=>{if(tab==='internet')return showInternetSettings166();$('#settings-internet166')?.classList.remove('primary');return selectSettingsBefore166(tab)};
refreshTransport16();
// The interface can start before the tunnel is ready. Resume a saved session
// after connectivity returns instead of leaving the employee at a stale login.
let internetResumeBusy166=false,internetResumeDenied166=false,internetResumeURL166=null;
setInterval(async()=>{
 if(user||internetResumeBusy166)return;
 internetResumeBusy166=true;
 try{
  const connection=await api('/api/connection');
  if(connection.host_url!==internetResumeURL166){internetResumeURL166=connection.host_url;internetResumeDenied166=false}
  if(!connection.host_found||!connection.profile||internetResumeDenied166)return;
  try{const data=await api('/api/me');if(!user)await enter(data.employee)}catch(e){if(e.status===401)internetResumeDenied166=true}
 }catch{}finally{internetResumeBusy166=false}
},2500);
