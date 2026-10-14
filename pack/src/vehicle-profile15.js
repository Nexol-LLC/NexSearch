/* Simplified axle setup: measured empty coupled loads plus cargo increments.
   Geometry is inherited from the chosen model; missing geometry stays missing. */
function axleCount15(vehicle){if(vehicle.trailer?.axleCount)return vehicle.trailer.axleCount;const drawing=typeof trailerDrawingLayout==='function'?trailerDrawingLayout(vehicle.id,vehicle.L):null;if(drawing)return drawing.xs.length;const name=vehicle.name?.ru||'';const n=name.match(/(\d+) ос/);return n?+n[1]:3}
function sumEmpty15(group){return group?.length&&group.every(a=>finiteNumber(a.emptyKg))?group.reduce((s,a)=>s+a.emptyKg,0):null}
function makeGroup15(count,center,spacing,total,label,limit){return Array.from({length:count},(_,i)=>({id:label+'-'+i,label:label+' '+(i+1),x:finiteNumber(center)?center+(i-(count-1)/2)*spacing:null,share:1/count,emptyKg:finiteNumber(total)?total/count:null,limitKg:finiteNumber(limit)&&limit>0?limit/count:null}))}
function preserveGroup15(previous,count,center,spacing,total,label,limit,oldSpacing){
 if(!previous||previous.length!==count||previous.some(a=>!finiteNumber(a.x)||!finiteNumber(a.share)))return makeGroup15(count,center,spacing,total,label,limit);
 const oldCenter=groupPosition(previous),oldTotal=sumEmpty15(previous),oldLimit=previous.reduce((s,a)=>s+(a.limitKg||0),0);
 const scale=oldSpacing>0&&spacing!==oldSpacing?spacing/oldSpacing:1;
 return previous.map((a,i)=>({...a,x:finiteNumber(center)?center+(a.x-oldCenter)*scale:null,
  emptyKg:finiteNumber(total)?total*(oldTotal>0?a.emptyKg/oldTotal:a.share):null,
  limitKg:finiteNumber(limit)&&limit>0?limit*(oldLimit>0?(a.limitKg||0)/oldLimit:a.share):null}));
}
function profileGear15(input){
 if(!['rigid','semitrailer'].includes(input.kind)||![input.rearCount,input.kind==='semitrailer'?input.trailerCount:1].every(n=>Number.isInteger(n)&&n>=1&&n<=16))throw Error('Проверьте количество осей');
 if(!finiteNumber(input.spacing)||input.spacing<0)throw Error('Проверьте расстояние между осями');
 const retain=input.groups?.kind===input.kind?input.groups:null;
 const front=preserveGroup15(retain?.front,retain?.front?.length||1,input.frontX,0,input.frontEmptyKg,'Передняя ось',input.frontLimitKg,0);
 const rear=preserveGroup15(retain?.rear,input.rearCount,input.rearX,input.spacing,input.rearEmptyKg,input.kind==='rigid'?'Задняя ось':'Ось тягача',input.rearLimitKg,input.originalSpacing);
 const gear={kind:input.kind,distributionSource:'Пустая машина: данные взвешивания. Доли нагрузки — из профиля; при создании новой группы равные доли.',geometryConfirmed:!!input.geometryConfirmed};
 if(input.kind==='rigid')return {...gear,front,rear};
 return {...gear,emptyCoupled:true,kingpinX:input.kingpinX,fifthWheelX:input.fifthWheelX,tractorFront:front,tractorRear:rear,trailerAxles:preserveGroup15(retain?.trailer,input.trailerCount,input.trailerX,input.spacing,input.trailerEmptyKg,'Ось прицепа',input.trailerLimitKg,input.originalSpacing)}
}
function defaultProfile15(v){
 const gear=v.runningGear,semi=gear?gear.kind==='semitrailer':!['van','truck5t'].includes(v.id)&&!(v.sections);
 if(gear&&(semi?[gear.tractorFront,gear.tractorRear,gear.trailerAxles]:[gear.front,gear.rear]).every(group=>Array.isArray(group)&&group.length)&&[...(semi?gear.tractorFront||[]:gear.front||[]),...(semi?gear.tractorRear||[]:gear.rear||[]),...(semi?gear.trailerAxles||[]:[])].length&&[...(semi?gear.tractorFront||[]:gear.front||[]),...(semi?gear.tractorRear||[]:gear.rear||[]),...(semi?gear.trailerAxles||[]:[])].every(a=>finiteNumber(a.x)&&finiteNumber(a.share))){const empty=grossAxleLoads(v,{weight:0,cgX:0}),front=semi?gear.tractorFront:gear.front,rear=semi?gear.tractorRear:gear.rear;const total=kind=>{if(empty.available)return empty.axles.filter(a=>a.kind===kind).reduce((s,a)=>s+a.kg,0);return sumEmpty15(kind.endsWith('front')?front:kind==='trailer'?gear.trailerAxles:rear)};const copy=(group,kind)=>group.map((a,i)=>({...a,emptyKg:empty.available?empty.axles.find(b=>b.kind===kind&&b.id===(a.id||kind+'-'+i))?.kg??a.emptyKg:a.emptyKg}));const limit=group=>group.every(a=>finiteNumber(a.limitKg)&&a.limitKg>0)?group.reduce((s,a)=>s+a.limitKg,0):null;return {kind:gear.kind,rearCount:rear.length,trailerCount:semi?gear.trailerAxles.length:3,frontX:groupPosition(front),rearX:groupPosition(rear),trailerX:semi?groupPosition(gear.trailerAxles):null,kingpinX:gear.kingpinX,fifthWheelX:gear.fifthWheelX,frontEmptyKg:total(semi?'tractor-front':'rigid-front'),rearEmptyKg:total(semi?'tractor-rear':'rigid-rear'),trailerEmptyKg:semi?total('trailer'):null,frontLimitKg:limit(front),rearLimitKg:limit(rear),trailerLimitKg:semi?limit(gear.trailerAxles):null,spacing:semi&&gear.trailerAxles.length>1?Math.abs(gear.trailerAxles[1].x-gear.trailerAxles[0].x):1340,geometryConfirmed:gear.geometryConfirmed!==false,groups:{kind:gear.kind,front:copy(front,semi?'tractor-front':'rigid-front'),rear:copy(rear,semi?'tractor-rear':'rigid-rear'),trailer:semi?copy(gear.trailerAxles,'trailer'):[]}}}
 const drawing=typeof trailerDrawingLayout==='function'?trailerDrawingLayout(v.id,v.L):null;
 const trailer=drawing?.xs.map((x,i)=>({id:'pdf-trailer-'+i,label:'Ось прицепа '+(i+1),x,share:1/drawing.xs.length,emptyKg:null,limitKg:null}));
 return {kind:semi?'semitrailer':'rigid',rearCount:1,trailerCount:axleCount15(v),frontX:semi?0:v.axles?.front??null,rearX:semi?3600:v.axles?.rear??null,trailerX:trailer?groupPosition(trailer):v.axles?.rear??null,kingpinX:drawing?.kingpinX??v.axles?.front??null,fifthWheelX:semi?3400:null,frontEmptyKg:null,rearEmptyKg:null,trailerEmptyKg:null,spacing:trailer?.length>1?trailer[1].x-trailer[0].x:1340,geometryConfirmed:false,groups:trailer?{kind:'semitrailer',trailer}:null}

}
if(typeof module!=='undefined')module.exports={profileGear15,makeGroup15,defaultProfile15};
