/* Default leadership overview. Personal mutations retain the original owner checks. */
let clientOwner167='',clientScopeUser167=null,clientTotal167=null;
const clientScope167=document.createElement('label');clientScope167.id='client-scope167';clientScope167.hidden=true;
clientScope167.innerHTML='<span>Ответственный</span><select id="client-owner167" aria-label="Ответственный за клиента"></select>';
$('#client-tools').before(clientScope167);
function clientOverview167(){return view==='clients'&&['manager','director'].includes(user?.role)&&!(readonly&&teamPerson)}
function clientOverviewQuery167(){return clientOwner167?'&employee_id='+encodeURIComponent(clientOwner167==='mine'?user.id:clientOwner167):''}
function drawClientOverview167(data){
 if(!clientOverview167())return;
 const select=$('#client-owner167');
 clientTotal167=data.owners.reduce((sum,p)=>sum+Number(p.client_count||0),0);
 select.innerHTML=`<option value="">${user.role==='manager'?'Все сотрудники отдела':'Все сотрудники'}</option><option value="mine">Только мои клиенты</option>`+data.owners.filter(p=>p.id!==user.id).map(p=>`<option value="${esc(p.id)}">${esc(p.first_name+' '+p.last_name)} · ${p.client_count}</option>`).join('');
 select.value=clientOwner167;
 if(select.value!==clientOwner167){clientOwner167='';select.value=''}
 $('#stages-edit').hidden=clientOwner167!=='mine';
 $('#export-link').href='/api/clients/overview/export?order='+encodeURIComponent($('#client-order').value)+clientOverviewQuery167();
 meta();
}
$('#client-owner167').onchange=async event=>{
 clientOwner167=event.target.value;$('#stage-filter').value='';$('#stages-edit').hidden=clientOwner167!=='mine';
 // Remove old cards immediately while the next employee scope is loading.
 rows=[];$('#kanban').replaceChildren();$('#table-body').replaceChildren();
 try{await load()}catch(error){notice(error.message,true)}
};
const showBefore167=show;show=async next=>{
 if(clientScopeUser167!==user?.id){clientOwner167='';clientTotal167=null;clientScopeUser167=user?.id}
 const work=showBefore167(next);
 clientScope167.hidden=!clientOverview167();
 if(clientOverview167())$('#stages-edit').hidden=clientOwner167!=='mine';
 await work;
};
const metaBefore167=meta;meta=()=>{
 metaBefore167();
 if(clientOverview167()){
  $('#page-meta').textContent=clientOwner167==='mine'?'Ваши клиенты':user.role==='manager'?'Клиенты сотрудников вашего отдела':'Клиенты всех сотрудников';
  if(clientTotal167!==null)$('#client-count').textContent=clientTotal167;
 }
};
const renderBefore167=render;render=()=>{
 renderBefore167();
 if(view!=='clients'||!['manager','director'].includes(user?.role))return;
 for(const node of $$('#kanban .kanban-card,#table-body tr[data-id]')){
  const company=rows.find(c=>c.id===node.dataset.id);if(!company)continue;
  const name=[company.owner_first_name,company.owner_last_name].filter(Boolean).join(' ');
  if(name&&!node.querySelector('.client-owner167')){
   const target=node.matches('tr')?node.firstElementChild:node.querySelector('h3');
   target?.insertAdjacentHTML('beforeend',`<small class="client-owner167">${esc(name)}${company.employee_id===user.id?' · вы':''}</small>`);
  }
  node.onclick=()=>openCompany162(company.id);
  if(node.matches('article')&&company.employee_id!==user.id){node.draggable=false;node.ondragstart=event=>event.preventDefault()}
 }
 for(const column of $$('#kanban .kanban-column')){
  const drop=column.ondrop;
  column.ondrop=event=>{
   const company=rows.find(c=>c.id===event.dataTransfer.getData('text/plain'));
   if(!company||company.employee_id!==user.id){event.preventDefault();column.classList.remove('drag-over');return}
   return drop?.(event);
  };
 }
};
