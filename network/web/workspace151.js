/* Working contact details belong to the employee's company card. */
actionNames['company.contacts']='Изменены рабочие контакты';
const companyBefore151=editCompany;
editCompany=company=>{
 companyBefore151(company);
 if(!company||readonly||company.employee_id!==user.id)return;
 const form=document.createElement('section');form.className='working-contacts151';
 form.innerHTML=`<h3>Актуальные контакты</h3><p class="muted">Укажите телефоны и почту, по которым удалось связаться. По одному контакту в строке; основной — первым.</p><div class="form-grid"><label>Телефоны<textarea id="working-phones151" rows="3" maxlength="4000" placeholder="+7 900 123-45-67">${esc((company.phones||[]).join('\n'))}</textarea></label><label>Почта<textarea id="working-emails151" rows="3" maxlength="4000" placeholder="logistics@example.ru">${esc((company.emails||[]).join('\n'))}</textarea></label></div><div class="working-contacts-actions151"><button type="button" id="save-contacts151">Сохранить контакты</button><span id="contacts-state151" role="status"></span></div>`;
 $('#editor-body .client-operations').before(form);
 const list=id=>$(id).value.split(/\r?\n/).map(value=>value.trim()).filter(Boolean);
 const saveContacts=async()=>{
  const phones=list('#working-phones151'),emails=list('#working-emails151');
  if(JSON.stringify(phones)===JSON.stringify(company.phones||[])&&JSON.stringify(emails)===JSON.stringify(company.emails||[]))return;
  const updated=await api('/api/leads/'+company.id+'/contacts',{method:'PATCH',body:JSON.stringify({phones,emails,revision:company.contacts_revision||0})});
  Object.assign(company,updated);
  const listed=rows.find(row=>row.id===company.id);if(listed)Object.assign(listed,updated);
  if(['search','clients'].includes(view))render();
  for(const [label,values] of [['Телефоны',company.phones],['Почта',company.emails]]){
   const row=$$('#editor-body .detail').find(row=>row.firstElementChild.textContent===label);
   const html=detail(label,values.join('\n'));
   if(row&&!html)row.remove();
   else if(row){const template=document.createElement('template');template.innerHTML=html;row.innerHTML=template.content.firstElementChild.innerHTML}
   else if(html)form.insertAdjacentHTML('beforebegin',html);
   if(label==='Почта'&&values.length){const mailRow=$$('#editor-body .detail').find(n=>n.firstElementChild?.textContent==='Почта');if(mailRow){const button=document.createElement('button');button.type='button';button.className='mail-write-company16';button.textContent='Написать';button.onclick=()=>composeMail16({to:company.emails});mailRow.lastElementChild.append(button)}}
  }
  $('#working-phones151').value=company.phones.join('\n');$('#working-emails151').value=company.emails.join('\n');
  $('#contacts-state151').textContent='Контакты сохранены';
 };
 $('#save-contacts151').onclick=async()=>{
  const button=$('#save-contacts151');button.disabled=true;$('#editor-error').textContent='';
  try{await saveContacts()}catch(error){$('#editor-error').textContent=error.message}finally{button.disabled=false}
 };
 const saveBefore151=editing;
 editing=async()=>{await saveContacts();if(saveBefore151)await saveBefore151()};
};
