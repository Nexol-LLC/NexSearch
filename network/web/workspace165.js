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
 if(contacts){const people=document.createElement('div');people.className='contact-people165';people.innerHTML='<h4>Контактные лица</h4><p class="muted">Загружаем…</p>';contacts.append(people);
  api('/api/contacts?company_id='+company.id).then(data=>{if(!people.isConnected)return;people.innerHTML='<h4>Контактные лица</h4>'+ (data.contacts.map(p=>`<article><strong>${esc(p.name)}</strong><span>${esc(p.position)}</span><p>${esc(p.phones)} ${esc(p.email)}</p><small>${esc(p.description)}</small></article>`).join('')||'<p class="muted">Контактных лиц пока нет. Добавить можно кнопкой «Доп. контакты» вверху карточки.</p>')}).catch(error=>{if(people.isConnected)people.innerHTML='<h4>Контактные лица</h4><p class="muted">'+esc(error.message)+'</p>'});
 }
 $('#editor').scrollTop=0;
};
document.addEventListener('click',event=>{const anchor=event.target.closest('#editor-body a[href="#"]');if(anchor){event.preventDefault();notice('В источнике указан некорректный адрес сайта.',true)}},true);
