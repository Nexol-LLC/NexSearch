/* Local VPN onboarding works before NexSearch login; keys never enter the page. */
const vpnPhase16={disabled:'Выключено',missing:'Нужен комплект с VPN-модулем',starting:'Запуск',connecting:'Подключение',needs_login:'Нужен вход',needs_approval:'Ожидает подтверждения',host_offline:'Хост не в сети',online:'VPN подключён',reconnecting:'Восстановление связи',error:'Ошибка подключения'};
function vpnLoginURL16(value){try{const url=new URL(value);return url.protocol==='https:'&&!url.username&&!url.password?url.href:null}catch{return null}}
const vpnAuth16=document.createElement('section');vpnAuth16.className='vpn-auth16';vpnAuth16.innerHTML='<strong>Работаете вне офиса?</strong><p>Один раз подключите компьютер к VPN-сети организации. Затем NexSearch будет подключаться автоматически.</p><button type="button" id="vpn-auth-enable16">Подключить VPN</button><p id="vpn-auth-status16" role="status"></p><a id="vpn-auth-login16" target="_blank" rel="noreferrer" hidden>Войти в VPN-сеть ↗</a><button type="button" id="vpn-auth-custom16" class="link">Указать свой Headscale</button>';
$('.auth-card').append(vpnAuth16);let vpnLocalState16=null;
$('#vpn-auth-enable16').onclick=async()=>{const button=$('#vpn-auth-enable16');button.disabled=true;try{await api('/api/client/vpn/config',{method:'POST',body:JSON.stringify({enabled:true,provider:'tailscale'})});await refreshVpnAuth16()}catch(error){$('#vpn-auth-status16').textContent=error.message}finally{button.disabled=false}};
$('#vpn-auth-custom16').onclick=()=>showVpnSetup16();
async function refreshVpnAuth16(){
 if(location.protocol==='https:'){vpnAuth16.hidden=true;return}
 try{
  const data=await api('/api/client/vpn/status');vpnLocalState16=data;
  vpnAuth16.hidden=false;$('#vpn-auth-status16').textContent=data.message||vpnPhase16[data.phase]||'';
  const url=vpnLoginURL16(data.auth_url);$('#vpn-auth-login16').hidden=!url;if(url)$('#vpn-auth-login16').href=url;
  $('#vpn-auth-enable16').hidden=!!data.config?.enabled;$('#vpn-auth-custom16').textContent=data.config?.enabled?'Настройки VPN':'Указать свой Headscale';
 }catch{vpnAuth16.hidden=true}
}
setInterval(()=>{if(!user)refreshVpnAuth16()},3000);refreshVpnAuth16();
const previousSettingsVpn16=openHostSettings;
openHostSettings=async()=>{await previousSettingsVpn16();if(!$('#host-settings-content'))return;const button=document.createElement('button');button.type='button';button.id='settings-vpn-tab16';button.textContent='VPN';$('#settings-update-tab').after(button);button.onclick=()=>showVpnSetup16(true)};

async function showVpnSetup16(existing=false){
 clearInterval(hostSettingsTimer);settingsEpoch++;settingsTab='vpn';const epoch=settingsEpoch;
 if(!existing)openEditor('Удалённое подключение','<div id="host-settings-content"></div>',null);
 $('#settings-host-tab')?.classList.remove('primary');$('#settings-update-tab')?.classList.remove('primary');$('#settings-vpn-tab16')?.classList.add('primary');
 const area=$('#host-settings-content');
 area.innerHTML=`<p class="muted">Хост и база остаются в офисе. VPN открывает NexSearch через защищённую сеть, сохраняя обычное подключение компьютера к интернету.</p><section class="vpn-panel16" id="vpn-local-panel16"><h3>Этот компьютер</h3><p id="vpn-local-message16" role="status">Проверяем VPN…</p><label>Сеть<select id="vpn-provider16"><option value="tailscale">Tailscale · сеть вашей организации</option><option value="headscale">Свой Headscale</option></select></label><label id="vpn-control-label16" hidden>Адрес Headscale<input id="vpn-control16" type="url" placeholder="https://vpn.example.ru"></label><div class="vpn-buttons16"><button type="button" id="vpn-connect16" class="primary">Подключить</button><button type="button" id="vpn-off16">Отключить</button><a id="vpn-login16" class="button" target="_blank" rel="noreferrer" hidden>Войти в VPN-сеть ↗</a></div><p class="muted">Сотрудник и офисный хост должны находиться в одной VPN-сети. После первого входа соединение восстанавливается автоматически.</p></section>${user?.role==='director'?'<section class="vpn-panel16" id="vpn-host-panel16"><h3>Офисный хост</h3><p id="vpn-host-message16" role="status">Проверяем модуль хоста…</p><div id="vpn-host-form16" hidden><label>Сеть хоста<select id="vpn-host-provider16"><option value="tailscale">Tailscale</option><option value="headscale">Свой Headscale</option></select></label><label id="vpn-host-control-label16" hidden>Адрес Headscale<input id="vpn-host-control16" type="url" placeholder="https://vpn.example.ru"></label><div class="vpn-buttons16"><button type="button" id="vpn-host-connect16">Подключить хост</button><button type="button" id="vpn-host-off16">Отключить хост</button><a id="vpn-host-login16" target="_blank" rel="noreferrer" hidden>Авторизовать офисный хост ↗</a></div><p class="muted">Хост подключается из офиса исходящим соединением. Первую авторизацию выполняет администратор вашей VPN-сети.</p></div></section>':''}<p id="vpn-error16" class="error" role="status"></p>`;
 let initializedLocal=false,initializedHost=false,editedLocal=false,editedHost=false;
 const current=()=>dialogOpen&&settingsTab==='vpn'&&settingsEpoch===epoch&&area.isConnected;
 const update=async()=>{
  if(!current())return;
  try{
   const state=await api('/api/client/vpn/status');if(!current())return;
   $('#vpn-local-message16').textContent=state.message+(state.address?' · '+state.address:'');
   if(!initializedLocal){if(!editedLocal){$('#vpn-provider16').value=state.config.provider;$('#vpn-control16').value=state.config.provider==='headscale'?state.config.control_url:'';$('#vpn-control-label16').hidden=state.config.provider!=='headscale'}initializedLocal=true}
   const url=vpnLoginURL16(state.auth_url);$('#vpn-login16').hidden=!url;if(url)$('#vpn-login16').href=url;
   $('#vpn-off16').disabled=!state.config.enabled;
  }catch{if(current()){$('#vpn-local-message16').textContent='Настройка VPN этого компьютера доступна в установленной программе сотрудника.';$('#vpn-connect16').disabled=$('#vpn-off16').disabled=true}}
  if(!user||user.role!=='director'||!current())return;
  try{
   const state=await api('/api/remote/status');if(!current())return;
   $('#vpn-host-message16').textContent=state.message+(state.address?' · '+state.address:'');
   $('#vpn-host-form16').hidden=!state.config;
   if(state.config&&!initializedHost){if(!editedHost){$('#vpn-host-provider16').value=state.config.provider;$('#vpn-host-control16').value=state.config.provider==='headscale'?state.config.control_url:'';$('#vpn-host-control-label16').hidden=state.config.provider!=='headscale'}initializedHost=true}
   const url=vpnLoginURL16(state.auth_url);$('#vpn-host-login16').hidden=!url;if(url)$('#vpn-host-login16').href=url;
  }catch(error){if(current())$('#vpn-host-message16').textContent=error.message}
 };
 const save=async(host,enabled)=>{
  const prefix=host?'vpn-host-':'vpn-',provider=$('#'+prefix+'provider16').value;
  $('#vpn-error16').textContent='';try{await api(host?'/api/remote/config':'/api/client/vpn/config',{method:'POST',body:JSON.stringify({enabled,provider,control_url:$('#'+prefix+'control16').value})});await update();refreshVpnAuth16()}catch(error){if(current())$('#vpn-error16').textContent=error.message}
 };
 $('#vpn-provider16').onchange=()=>{editedLocal=true;$('#vpn-control-label16').hidden=val('#vpn-provider16')!=='headscale'};
 $('#vpn-control16').oninput=()=>{editedLocal=true};
 $('#vpn-connect16').onclick=()=>save(false,true);$('#vpn-off16').onclick=()=>save(false,false);
 if($('#vpn-host-provider16')){$('#vpn-host-provider16').onchange=()=>{editedHost=true;$('#vpn-host-control-label16').hidden=val('#vpn-host-provider16')!=='headscale'};$('#vpn-host-control16').oninput=()=>{editedHost=true};$('#vpn-host-connect16').onclick=()=>save(true,true);$('#vpn-host-off16').onclick=()=>save(true,false)}
 await update();hostSettingsTimer=setInterval(()=>{if(!current()){clearInterval(hostSettingsTimer);return}update()},2000);
}
const selectSettingsBeforeVpn16=selectSettingsTab;selectSettingsTab=tab=>{$('#settings-vpn-tab16')?.classList.remove('primary');selectSettingsBeforeVpn16(tab)};
