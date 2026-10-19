/* Mail groups belong to an account and folder; HTML stays inside the scriptless frame. */
let mailRelated163=[];
const mailOpenGroups163=new Set(),mailClosedFolders163=new Set();
const mailScope163=()=>[user?.id,mailAccount16,mailFolder16].join('/');
let mailListScope163='';
const allMail163=()=>{const seen=new Set();return [...mailItems16,...mailRelated163].filter(m=>!seen.has(m.id)&&seen.add(m.id))};
function address163(value){const match=String(value||'').match(/[^\s<>"(),;]+@[^\s<>"(),;]+/);return match?match[0].toLowerCase():''}
function outgoing163(message){const a=accountNow16();return a?.folders.some(f=>f.sent&&f.id===message.folder)||[a?.email,a?.login].filter(Boolean).map(v=>v.toLowerCase()).includes(address163(message.sender))}
function partner163(message){const own=String(accountNow16()?.email||'').toLowerCase(),sender=address163(message.sender);return !outgoing163(message)&&sender?sender:(message.recipients||[]).map(address163).find(a=>a&&a!==own)||'message:'+message.id}
function mailGroups163(){
 const groups=new Map(),base=new Set(mailItems16.map(m=>partner163(m)));
 for(const m of allMail163()){
  const key=partner163(m);if(!base.has(key))continue;
  if(!groups.has(key))groups.set(key,{key,messages:[],seenIds:new Set()});const group=groups.get(key);
  if(m.message_id&&group.seenIds.has(m.message_id))continue;
  if(m.message_id)group.seenIds.add(m.message_id);group.messages.push(m);
 }
 for(const group of groups.values()){
  group.messages.sort((a,b)=>(Date.parse(a.sent_at)||0)-(Date.parse(b.sent_at)||0)||a.uid-b.uid);
  group.latest=group.messages.at(-1);group.unread=group.messages.filter(m=>!m.seen).length;
  const incoming=group.messages.find(m=>address163(m.sender)===group.key);
  group.name=incoming?.sender.replace(/<[^>]*>/g,'').replace(/"/g,'').trim()||group.key;
 }
 return [...groups.values()].sort((a,b)=>(Date.parse(b.latest.sent_at)||0)-(Date.parse(a.latest.sent_at)||0)||b.latest.uid-a.latest.uid);
}
function replyDepth163(message,messages){
 const byId=new Map(messages.filter(m=>m.message_id).map(m=>[m.message_id,m])),visited=new Set([message.id]);let depth=0,current=message;
 while(depth<5){const refs=[...(current.reference_ids||[]),current.in_reply_to].filter(Boolean).reverse();const parent=refs.map(r=>byId.get(r)).find(p=>p&&!visited.has(p.id));if(!parent)break;visited.add(parent.id);depth++;current=parent}
 return depth;
}
function mailRow163(m,depth=0){
 const outgoing=outgoing163(m),name=outgoing?'Вы':m.sender.replace(/<[^>]*>/g,'').replace(/"/g,'').trim()||m.sender;
 return `<article class="mail-row16 mail-child163 ${m.seen?'':'unread'}" style="--reply-depth:${depth}"><input type="checkbox" data-mail-check16="${esc(m.id)}" aria-label="Выбрать ${esc(m.subject)}" ${mailWS16.selected.has(m.id)?'checked':''}><button type="button" data-mail-id16="${esc(m.id)}"><span class="mail-avatar16">${depth?'↳':outgoing?'↑':esc(name[0]||'✉')}</span><span class="mail-row-sender16" title="${esc(m.sender)}">${esc(name)}</span><strong>${esc(m.subject||'Без темы')}${outgoing?'<small class="mail-outgoing163">Ваш ответ</small>':''}</strong><time title="${esc(m.sent_at?dateFull(m.sent_at):'')}">${mailShortDate16(m.sent_at)}</time></button></article>`;
}
mailRowsDraw16=()=>{
 const area=$('#mail-items16');if(!area)return;const groups=mailGroups163();
 area.innerHTML=groups.map(group=>{
  if(group.messages.length===1)return mailRow163(group.latest);
  const key=mailScope163()+'/'+group.key,open=mailOpenGroups163.has(key),count=group.messages.length;
  return `<section class="mail-group163"><button type="button" class="mail-group-head163 ${group.unread?'unread':''}" data-mail-group163="${esc(group.key)}" aria-expanded="${open}" aria-label="${open?'Свернуть':'Развернуть'} переписку с ${esc(group.name)}: ${count} писем"><span class="mail-chevron163">›</span><span class="mail-avatar16">${esc(group.name[0]||'✉')}</span><span class="mail-group-name163"><strong>${esc(group.name)}</strong><small>${esc(group.key)}</small></span><span class="mail-group-preview163">${esc(group.latest.subject||'Без темы')}</span><span class="mail-group-count163">${count}${group.unread?` · новых ${group.unread}`:''}</span><time>${mailShortDate16(group.latest.sent_at)}</time></button>${open?`<div class="mail-group-messages163">${group.messages.map(m=>mailRow163(m,replyDepth163(m,group.messages))).join('')}</div>`:''}</section>`;
 }).join('')||'<div class="empty">Писем не найдено</div>';
 area.querySelectorAll('[data-mail-group163]').forEach(b=>b.onclick=()=>{const key=mailScope163()+'/'+b.dataset.mailGroup163;mailOpenGroups163.has(key)?mailOpenGroups163.delete(key):mailOpenGroups163.add(key);mailRowsDraw16()});
 area.querySelectorAll('[data-mail-id16]').forEach(b=>b.onclick=()=>readMail16(b.dataset.mailId16));
 area.querySelectorAll('[data-mail-check16]').forEach(c=>c.onchange=()=>{c.checked?mailWS16.selected.add(c.dataset.mailCheck16):mailWS16.selected.delete(c.dataset.mailCheck16);mailSelectedDraw16()});
 mailSelectedDraw16();const select=$('#mail-select-all16');if(select)select.onchange=e=>{mailWS16.selected=new Set(e.target.checked?allMail163().map(m=>m.id):[]);mailRowsDraw16()};
};
mailSelectedDraw16=()=>{const n=mailWS16.selected.size,all=allMail163();if(!$('#mail-selected-count16'))return;$('#mail-selected-count16').textContent='Выбрано: '+n;$('#mail-mark-read16').disabled=$('#mail-mark-unread16').disabled=!n;$('#mail-select-all16').checked=!!all.length&&n===all.length;$('#mail-select-all16').indeterminate=!!n&&n<all.length};
loadMailItems16=async()=>{
 const seq=++mailWS16.sequence,ticket=mailRequest16,account=mailAccount16,folder=mailFolder16,query=mailQuery16,unread=mailWS16.unread;
 const data=await api('/api/mail/messages?'+new URLSearchParams({account_id:account,folder,q:query,unread,limit:Math.min(500,Math.max(100,mailItems16.length))}));
 if(seq!==mailWS16.sequence||ticket!==mailRequest16||view!=='mail'||account!==mailAccount16||folder!==mailFolder16||query!==mailQuery16||!$('#mail-items16'))return;
 const scope=mailScope163()+'/'+query+'/'+unread,older=mailListScope163===scope&&mailItems16.length>500?mailItems16.filter(m=>m.uid<Math.min(...data.messages.map(m=>m.uid))):[];
 mailListScope163=scope;mailItems16=[...data.messages,...older];mailRelated163=data.related||[];const available=new Set(allMail163().map(m=>m.id));mailWS16.selected=new Set([...mailWS16.selected].filter(id=>available.has(id)));mailRowsDraw16();$('#mail-older16').disabled=!data.messages.length;$('#mail-older16').textContent='Загрузить более ранние';
};
loadOlderMail16=async()=>{
 const button=$('#mail-older16'),account=mailAccount16,folder=mailFolder16,ticket=mailRequest16,sequence=++mailWS16.sequence,oldest=mailItems16.length?Math.min(...mailItems16.map(m=>m.uid)):null;if(!oldest)return;button.disabled=true;button.textContent='Загружаем…';
 try{const query=new URLSearchParams({account_id:account,folder,q:mailQuery16,unread:mailWS16.unread,before_uid:oldest});let data=await api('/api/mail/messages?'+query);
  if(!data.messages.length&&!mailQuery16){await api('/api/mail/accounts/'+account+'/sync',{method:'POST',body:JSON.stringify({folder,before_uid:oldest})});data=await api('/api/mail/messages?'+query)}
  if(view!=='mail'||ticket!==mailRequest16||sequence!==mailWS16.sequence||account!==mailAccount16||folder!==mailFolder16)return;
  const known=new Set(mailItems16.map(m=>m.id));mailItems16.push(...data.messages.filter(m=>!known.has(m.id)));const related=new Map(mailRelated163.map(m=>[m.id,m]));for(const m of data.related||[])related.set(m.id,m);mailRelated163=[...related.values()];mailRowsDraw16();button.textContent=data.messages.length?'Загрузить более ранние':'Более ранних писем нет';button.disabled=!data.messages.length;
 }catch(error){if(button.isConnected){button.disabled=false;button.textContent='Повторить загрузку'}throw error}
};
const presentBefore163=presentMail16;
presentMail16=message=>{const related=mailRelated163.find(m=>m.id===message.id);if(related)related.seen=message.seen;presentBefore163(message)};

function folderForest163(folders){
 const nodes=new Map();
 for(const f of folders){
  const parts=f.delimiter?f.id.split(f.delimiter):[f.id],display=String(f.name||f.id),names=f.delimiter?display.split(f.delimiter):[display];let parent=null;
  for(let i=0;i<parts.length;i++){
   const id=parts.slice(0,i+1).join(f.delimiter||'');if(!nodes.has(id))nodes.set(id,{id,name:names[i]||parts[i],selectable:false,children:[],parent});
   const node=nodes.get(id);if(i===parts.length-1)Object.assign(node,f,{name:f.id.toUpperCase()==='INBOX'?'Входящие':names[i]||parts[i],parent});parent=id;
  }
 }
 const roots=[];for(const node of nodes.values())(node.parent&&nodes.has(node.parent)?nodes.get(node.parent).children:roots).push(node);
 const sort=list=>{list.sort((a,b)=>(a.id.toUpperCase()==='INBOX'?-1:b.id.toUpperCase()==='INBOX'?1:0)||a.name.localeCompare(b.name,'ru'));for(const n of list)sort(n.children)};sort(roots);return roots;
}
function folder_name163(value){try{return folderTitle16({id:value,name:value,sent:false})}catch{return value}}
foldersDraw16=()=>{
 const account=accountNow16(),area=$('#mail-folders16');if(!account||!area)return;
 const forest=folderForest163(account.folders||[]),scope=user?.id+'/'+account.id+'/';
 const row=(node,depth)=>{const expanded=!mailClosedFolders163.has(scope+node.id),count=account.folder_counts?.[node.id]?.unread||0;
  return `<div class="mail-folder-row163 ${node.id===mailFolder16?'active':''}" style="--folder-depth:${depth}">${node.children.length?`<button type="button" class="mail-folder-arrow163" data-folder-toggle163="${esc(node.id)}" aria-expanded="${expanded}" aria-label="${expanded?'Свернуть':'Развернуть'} папку ${esc(node.name)}">›</button>`:'<span class="mail-folder-spacer163"></span>'}<button type="button" class="mail-folder-title163" data-folder16="${esc(node.id)}" ${node.selectable===false?'disabled':''}><span class="folder-icon16">${node.id.toUpperCase()==='INBOX'?'▣':node.sent?'↗':'▱'}</span><span>${esc(node.name)}</span>${count?`<b>${count}</b>`:''}</button></div>`};
 const branch=(node,depth)=>row(node,depth)+(!mailClosedFolders163.has(scope+node.id)?node.children.map(n=>branch(n,depth+1)).join(''):'');
 const inbox=forest.find(n=>n.id.toUpperCase()==='INBOX');
 area.innerHTML=(inbox?`<div class="mail-inbox163">${row(inbox,0)}</div>`:'')+'<div class="mail-folder-tree163">'+(inbox&&!mailClosedFolders163.has(scope+inbox.id)?inbox.children.map(n=>branch(n,1)).join(''):'')+forest.filter(n=>n!==inbox).map(n=>branch(n,0)).join('')+'</div>';
 area.querySelectorAll('[data-folder-toggle163]').forEach(b=>b.onclick=()=>{const key=scope+b.dataset.folderToggle163;mailClosedFolders163.has(key)?mailClosedFolders163.delete(key):mailClosedFolders163.add(key);foldersDraw16()});
 area.querySelectorAll('[data-folder16]').forEach(b=>b.onclick=async()=>{if(mailFolder16===b.dataset.folder16)return;mailFolder16=b.dataset.folder16;mailQuery16='';mailWS16.message=null;mailWS16.selected.clear();mailRelated163=[];try{await loadMail16();await syncVisibleMail16()}catch(e){mailError16(e)}});
 const status=$('#mail-sync-status16');if(status)status.textContent=mailboxInfo16(account);
};

const renderBefore163=renderMailContent16;
renderMailContent16=(message,container)=>{
 renderBefore163(message,container);const configured=new WeakSet(),controls=container.querySelector('.mail-format-controls16');if(!controls)return;
 const setup=()=>{const frame=container.querySelector('.mail-html-frame16');if(!frame||configured.has(frame))return;configured.add(frame);
  frame.addEventListener('load',async()=>{
   let doc;try{doc=frame.contentDocument}catch{return}const images=[...doc.querySelectorAll('img[data-mail-image]')];if(!images.length)return;
   const state=document.createElement('small');state.className='mail-images-status163';state.setAttribute('role','status');controls.append(state);
   let remaining=images.slice(),failed=[];
   const load=async()=>{failed=[];state.textContent='Загружаем изображения…';let cursor=0;
    const worker=async()=>{while(cursor<remaining.length){const img=remaining[cursor++];try{
      const response=await fetch('/api/mail/messages/'+encodeURIComponent(message.id)+'/images/'+img.dataset.mailImage);if(!response.ok)throw Error('image');const blob=await response.blob();
      const uri=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(blob)});
      if(!frame.isConnected)return;img.src=uri;img.removeAttribute('data-mail-remote');
     }catch{failed.push(img);img.alt=img.alt||'Изображение временно недоступно'}}};
    await Promise.all(Array.from({length:Math.min(4,remaining.length)},worker));if(!state.isConnected)return;state.replaceChildren();
    if(failed.length){state.append(document.createTextNode('Не загружено изображений: '+failed.length+' '));const retry=document.createElement('button');retry.type='button';retry.textContent='Повторить';retry.onclick=()=>{remaining=failed.slice();load()};state.append(retry)}
   };await load();
  },{once:true});
 };
 controls.addEventListener('click',event=>{if(event.target.closest('[data-mail-format16]')){controls.querySelectorAll('.mail-images-status163').forEach(n=>n.remove());setup()}});setup();
};
const settingsBefore163=mailSettings16;
mailSettings16=async()=>{await settingsBefore163();for(const f of accountNow16()?.folders||[])if(f.selectable===false)for(const box of $$('[data-sync-folder16]'))if(box.dataset.syncFolder16===f.id){box.disabled=true;box.checked=false}};
const logoutBefore163=signOut;
signOut=()=>{mailRelated163=[];mailOpenGroups163.clear();mailClosedFolders163.clear();logoutBefore163()};
