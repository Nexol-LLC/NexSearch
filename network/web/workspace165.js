/* One scrollable company sheet, explicit source links and timezone criteria. */
const zoneChoices165=Array.from({length:11},(_,i)=>i+2);
function timezoneLabel165(hours){const delta=hours-3;return `UTC+${hours} · МСК${delta?delta>0?'+'+delta:String(delta):''}`}
const timezoneCriteria165=document.createElement('fieldset');timezoneCriteria165.id='timezone-criteria165';timezoneCriteria165.innerHTML='<legend>По часовым поясам</legend><p class="muted">Можно выбрать несколько. Если ничего не выбрано — любой пояс. Используется регион регистрации; компании с неопределённым поясом не попадут в выбранные.</p><div id="timezone-choices165">'+zoneChoices165.map(h=>`<label><input type="checkbox" value="${h}">${timezoneLabel165(h)}</label>`).join('')+'</div>';
$('#criterion-region').closest('.form-grid').after(timezoneCriteria165);
const criteriaBefore165=buildCriteria;buildCriteria=()=>{criteriaBefore165();for(const box of $$('#timezone-choices165 input'))box.checked=(criteria.timezones||[]).includes(+box.value);const count=Number($('#criteria-count').textContent||0)+(criteria.timezones?.length||0);$('#criteria-count').textContent=count||''};

function sourceLink165(label,raw){
 const url=safe(raw);if(url==='#')return `<div class="company-source165"><span>${esc(label)}</span><small>Корректный адрес не указан</small></div>`;
 return `<div class="company-source165"><span>${esc(label)}</span><a href="${esc(url)}" target="_blank" rel="noopener noreferrer" title="${esc(url)}">${esc(new URL(url).hostname)} ↗</a>${copyButton(url,'Скопировать ссылку')}</div>`;
}
function contactValues165(value){return [...new Set((Array.isArray(value)?value:String(value||'').split(/[\n;,]+/)).map(item=>String(item).trim()).filter(Boolean))]}
function contactButtons165(phones,emails){
 const group=(label,values)=>values.length?`<div class="added-contact-values165"><span>${label}</span><div>${values.map(value=>copyButton(value)).join('')}</div></div>`:'';
 return group('Телефоны',contactValues165(phones))+group('Почта',contactValues165(emails));
}
function contactPerson165(person){return `<article class="added-contact-person165"><strong>${esc(person.name||'Контактное лицо')}</strong>${person.position?`<small>${esc(person.position)}</small>`:''}${contactButtons165(person.phones,person.email)}${person.description?`<p>${esc(person.description)}</p>`:''}</article>`}
function refreshAddedContacts165(company){
 const block=$('#added-contacts165');if(!block||block.dataset.companyId!==company.id)return;
 const working=company.contacts_revision>0?contactButtons165(company.phones,company.emails):'';
 const people=(block.contactPeople165||[]).map(contactPerson165).join('');
 block.innerHTML='<h4>Добавленные контакты</h4>'+(working?`<article class="added-contact-person165"><strong>Рабочие контакты компании</strong>${working}</article>`:'')+people+(block.contactLoading165?'<p class="muted">Загружаем контактных лиц…</p>':'')+(!working&&!people&&!block.contactLoading165?'<p class="muted">Пока не добавлены. Сохраните актуальные номера и почту ниже или добавьте контактное лицо кнопкой «Доп. контакты».</p>':'')+(block.contactError165?`<p class="error">${esc(block.contactError165)}</p><button type="button" id="retry-contacts165">Повторить загрузку</button>`:'');
 const retry=$('#retry-contacts165');if(retry)retry.onclick=()=>loadAddedContacts165(company,block);
}
async function loadAddedContacts165(company,block){
 block.contactLoading165=true;block.contactError165='';refreshAddedContacts165(company);
 try{const data=await api('/api/contacts?company_id='+company.id);if(!block.isConnected)return;block.contactPeople165=data.contacts}
 catch(error){if(!block.isConnected)return;block.contactError165=error.message}
 finally{if(block.isConnected){block.contactLoading165=false;refreshAddedContacts165(company)}}
}
showContacts=async company=>{
 const ticket=nav,rev=editorRevision16;
 try{
  const data=await api('/api/contacts?company_id='+company.id);
  if(ticket!==nav||rev!==editorRevision16)return;
  const canEdit=company.employee_id===user?.id;
  openEditor('Добавленные контакты · '+company.name,`${canEdit?'<button type="button" id="contact-add">+ Контактное лицо</button>':''}${data.contacts.map(contactPerson165).join('')||'<div class="empty">Контактных лиц пока нет</div>'}`,null);
  editorBack162(()=>reloadCompany162(company),'← К компании');
  if(canEdit)$('#contact-add').onclick=()=>{
   openEditor('Добавить контактное лицо',`${field('contact-name','Имя','','text','required maxlength="200"')}${field('contact-position','Должность','','text','maxlength="200"')}<label>Телефоны<textarea id="contact-phones" rows="3" maxlength="1000" placeholder="Каждый номер с новой строки"></textarea></label>${field('contact-email','Почта','','email','multiple maxlength="300"')}<label>Описание<textarea id="contact-note" rows="3" maxlength="3000"></textarea></label>`,async()=>{
    await api('/api/contacts',{method:'POST',body:JSON.stringify({company_id:company.id,name:val('#contact-name').trim(),position:val('#contact-position').trim(),phones:val('#contact-phones').trim(),email:val('#contact-email').trim(),description:val('#contact-note').trim()})});
    return ()=>reloadCompany162(company);
   });
   editorBack162(()=>showContacts(company),'← К контактам');
  };
 }catch(error){if(ticket===nav&&rev===editorRevision16)asyncError16(error)}
};
const companyBefore165=editCompany;editCompany=(company,back=null)=>{
 companyBefore165(company,back);if(!company)return;
 const overview=$('[data-company-section165="overview"]');
 // Replace the legacy website link, which did not accept scheme-less addresses.
 const website=[...$('#editor-body').querySelectorAll('.detail')].find(n=>n.firstElementChild?.textContent==='Сайт');website?.remove();
 if(company.website)overview.insertAdjacentHTML('beforeend',sourceLink165('Сайт компании',company.website));
 const sources=[];const seen=new Set();
 for(const s of [...(company.sources||[]),{name:company.source,url:company.source_url},{name:'Проверка Чекко / ФНС',url:company.verification?.source_url}]){
  if(!s?.url)continue;const url=safe(s.url);if(seen.has(url==='#'?String(s.url):url))continue;seen.add(url==='#'?String(s.url):url);sources.push(sourceLink165(s.name||'Источник',s.url));
 }
 if(sources.length)overview.insertAdjacentHTML('beforeend',`<div class="company-sources165"><h4>Источники информации</h4>${sources.join('')}</div>`);
 const contacts=$('[data-company-section165="contacts"]');
 if(contacts){const added=document.createElement('div');added.id='added-contacts165';added.className='added-contacts165';added.dataset.companyId=company.id;contacts.insertBefore(added,contacts.firstElementChild.nextSibling);loadAddedContacts165(company,added);
 }
 $('#editor').scrollTop=0;
};
document.addEventListener('click',event=>{const anchor=event.target.closest('#editor-body a[href="#"]');if(anchor){event.preventDefault();notice('В источнике указан некорректный адрес сайта.',true)}},true);
