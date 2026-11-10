export const sections = [
 ['overview','Рабочий стол','◈'],['search','Поиск клиентов','⌕'],['clients','Клиентская база','▦'],['carriers','Перевозчики','⇄'],['tasks','Задачи','✓'],['mail','Почта','✉'],['loading','Погрузка','▱'],['services','Мои сервисы','⊞'],['team','Сотрудники','♧'],['settings','Настройки','⚙']
];
export class Navigation {
 constructor(){this.section='overview';this.generation=0}
 navigate(section){if(!sections.some(([id])=>id===section))throw Error('Раздел не найден');this.section=section;this.generation++;return this.ticket()}
 ticket(){return {section:this.section,generation:this.generation}}
 accepts(ticket){return ticket.section===this.section&&ticket.generation===this.generation}
}
export const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function searchCriteria(form){
 const count=Number(form.count),inn=String(form.inn||'').trim();
 if(!Number.isInteger(count)||count<1||count>50)throw Error('Укажите от 1 до 50 компаний');
 if(inn&&!/^(\d{10}|\d{12})$/.test(inn))throw Error('ИНН должен содержать 10 или 12 цифр');
 const limits={all:[null,null],small:[10e6,100e6],medium:[100e6,500e6],large:[500e6,1e9],largest:[1e9,null]}[form.revenue||'all'];
 if(!limits)throw Error('Неизвестный диапазон выручки');
 return {count,inn:inn||null,industries:form.industry?[form.industry]:[],timezones:form.timezone?[form.timezone]:[],revenue_min:limits[0],revenue_max:limits[1]};
}
