/* Simplified axle setup: measured empty coupled loads plus cargo increments.
   Geometry is inherited from the chosen model; missing geometry stays missing. */
function axleCount15(vehicle){const name=vehicle.name?.ru||'';const n=name.match(/(\d+) ос/);return n?+n[1]:vehicle.id==='pdf-lowbed5'?5:vehicle.id==='pdf-lowbed7'?7:3}
function sumEmpty15(group){return group?.length&&group.every(a=>finiteNumber(a.emptyKg))?group.reduce((s,a)=>s+a.emptyKg,0):null}
function makeGroup15(count,center,spacing,total,label,limit){return Array.from({length:count},(_,i)=>({id:label+'-'+i,label:label+' '+(i+1),x:finiteNumber(center)?center+(i-(count-1)/2)*spacing:null,share:1/count,emptyKg:finiteNumber(total)?total/count:null,limitKg:finiteNumber(limit)&&limit>0?limit/count:null}))}
function profileGear15(input){
 if(!['rigid','semitrailer'].includes(input.kind)||![input.rearCount,input.kind==='semitrailer'?input.trailerCount:1].every(n=>Number.isInteger(n)&&n>=1&&n<=16))throw Error('Проверьте количество осей');
 if(!finiteNumber(input.spacing)||input.spacing<0)throw Error('Проверьте расстояние между осями');
 const front=makeGroup15(1,input.frontX,0,input.frontEmptyKg,'Передняя ось',input.frontLimitKg);
 const rear=makeGroup15(input.rearCount,input.rearX,input.spacing,input.rearEmptyKg,input.kind==='rigid'?'Задняя ось':'Ось тягача',input.rearLimitKg);
 const gear={kind:input.kind,distributionSource:'Пустая машина: данные взвешивания. В группе осей — равные доли; уточняются по подвеске.',geometryConfirmed:!!input.geometryConfirmed};
 if(input.kind==='rigid')return {...gear,front,rear};
 return {...gear,emptyCoupled:true,kingpinX:input.kingpinX,fifthWheelX:input.fifthWheelX,tractorFront:front,tractorRear:rear,trailerAxles:makeGroup15(input.trailerCount,input.trailerX,input.spacing,input.trailerEmptyKg,'Ось прицепа',input.trailerLimitKg)}
}
function defaultProfile15(v){
 const gear=v.runningGear,semi=gear?gear.kind==='semitrailer':!['van','truck5t'].includes(v.id)&&!(v.sections);
 if(gear&&!gearErrors(gear).length){const empty=grossAxleLoads(v,{weight:0,cgX:0}),front=semi?gear.tractorFront:gear.front,rear=semi?gear.tractorRear:gear.rear;const total=kind=>empty.axles.filter(a=>a.kind===kind).reduce((s,a)=>s+a.kg,0);const limit=group=>group.every(a=>finiteNumber(a.limitKg)&&a.limitKg>0)?group.reduce((s,a)=>s+a.limitKg,0):null;return {kind:gear.kind,rearCount:rear.length,trailerCount:semi?gear.trailerAxles.length:3,frontX:groupPosition(front),rearX:groupPosition(rear),trailerX:semi?groupPosition(gear.trailerAxles):null,kingpinX:gear.kingpinX,fifthWheelX:gear.fifthWheelX,frontEmptyKg:total(semi?'tractor-front':'rigid-front'),rearEmptyKg:total(semi?'tractor-rear':'rigid-rear'),trailerEmptyKg:semi?total('trailer'):null,frontLimitKg:limit(front),rearLimitKg:limit(rear),trailerLimitKg:semi?limit(gear.trailerAxles):null,spacing:semi&&gear.trailerAxles.length>1?Math.abs(gear.trailerAxles[1].x-gear.trailerAxles[0].x):1340,geometryConfirmed:gear.geometryConfirmed!==false}}
 return {kind:semi?'semitrailer':'rigid',rearCount:semi?1:1,trailerCount:axleCount15(v),frontX:semi?0:v.axles?.front??null,rearX:semi?3600:v.axles?.rear??null,trailerX:v.axles?.rear??null,kingpinX:v.axles?.front??null,fifthWheelX:semi?3400:null,frontEmptyKg:null,rearEmptyKg:null,trailerEmptyKg:null,spacing:1340,geometryConfirmed:false}
}
if(typeof module!=='undefined')module.exports={profileGear15,makeGroup15,defaultProfile15};
