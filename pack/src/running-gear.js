/* Gross static axle loads. Group distribution is supplied/selected explicitly.
   Units: x in mm, forces displayed as equivalent mass in kg under gravity. */
function finiteNumber(v){return typeof v==='number'&&Number.isFinite(v)}
function gearErrors(gear){
 const errors=[];if(!gear||!['rigid','semitrailer'].includes(gear.kind))return ['Заполните параметры тягача и прицепа'];
 if(gear.geometryConfirmed===false)errors.push('Подтвердите схему осей выбранной машины');
 const validAxle=a=>a&&finiteNumber(a.x)&&finiteNumber(a.share)&&a.share>0&&a.share<=1;
 const groups=gear.kind==='semitrailer'?[gear.trailerAxles,gear.tractorFront,gear.tractorRear]:[gear.front,gear.rear];
 groups.forEach((g,i)=>{if(!Array.isArray(g)||!g.length||g.some(a=>!validAxle(a)))errors.push('Проверьте координаты и доли нагрузки группы '+(i+1));else if(Math.abs(g.reduce((s,a)=>s+a.share,0)-1)>1e-6)errors.push('Сумма долей нагрузки группы должна быть 1');});
 if(gear.kind==='semitrailer'){
  if(!finiteNumber(gear.kingpinX)||!finiteNumber(gear.fifthWheelX))errors.push('Нужно положение седла тягача и прицепа');
  if(!gear.emptyCoupled&&(!finiteNumber(gear.trailerTareKg)||gear.trailerTareKg<0||!finiteNumber(gear.trailerTareCgX)))errors.push('Нужны масса и центр массы пустого прицепа');
  if(gear.emptyCoupled&&(gear.trailerAxles||[]).some(a=>!finiteNumber(a.emptyKg)||a.emptyKg<0))errors.push('Укажите нагрузку пустого автопоезда на тележку прицепа');
  if([...(gear.tractorFront||[]),...(gear.tractorRear||[])].some(a=>!finiteNumber(a.emptyKg)||a.emptyKg<0))errors.push('Укажите измеренные нагрузки пустого тягача');
 }else if([...(gear.front||[]),...(gear.rear||[])].some(a=>!finiteNumber(a.emptyKg)||a.emptyKg<0))errors.push('Укажите нагрузки пустого автомобиля');
 return [...new Set(errors)];
}
function groupPosition(group){return group.reduce((s,a)=>s+a.x*a.share,0)}
function supportReactions(weight,cg,front,rear){if(!(rear>front))return null;const r=weight*(cg-front)/(rear-front);return {front:weight-r,rear:r}}
function distribute(group,reaction,base,section,kind){return group.map((a,i)=>{const kg=reaction*a.share+(base?a.emptyKg:0);return {id:a.id||kind+'-'+i,label:a.label||'Ось '+(i+1),section,kind,x:a.x,kg,limitKg:finiteNumber(a.limitKg)&&a.limitKg>0?a.limitKg:null,over:finiteNumber(a.limitKg)&&a.limitKg>0&&kg>a.limitKg,unstable:kg<0,share:a.share}})}
function grossAxleLoads(vehicle,stats){
 const g=vehicle.runningGear,errors=gearErrors(g);if(errors.length)return {available:false,errors,axles:[]};
 let axles=[],reaction,kingpinKg=null;
 if(g.kind==='rigid'){
  reaction=supportReactions(stats.weight,stats.cgX,groupPosition(g.front),groupPosition(g.rear));
  if(!reaction)return {available:false,errors:['Задняя опора должна быть позади передней'],axles:[]};
  axles=[...distribute(g.front,reaction.front,true,0,'rigid-front'),...distribute(g.rear,reaction.rear,true,0,'rigid-rear')];
 }else{
  const gross=g.emptyCoupled?stats.weight:stats.weight+g.trailerTareKg;const cg=g.emptyCoupled?stats.cgX:gross?(stats.weight*stats.cgX+g.trailerTareKg*g.trailerTareCgX)/gross:0;
  reaction=supportReactions(gross,cg,g.kingpinX,groupPosition(g.trailerAxles));
  if(!reaction)return {available:false,errors:['Группа осей прицепа должна быть позади седла'],axles:[]};
  kingpinKg=reaction.front;
  const tractor=supportReactions(kingpinKg,g.fifthWheelX,groupPosition(g.tractorFront),groupPosition(g.tractorRear));
  if(!tractor)return {available:false,errors:['Проверьте координаты осей тягача'],axles:[]};
  axles=[...distribute(g.tractorFront,tractor.front,true,0,'tractor-front'),...distribute(g.tractorRear,tractor.rear,true,0,'tractor-rear'),...distribute(g.trailerAxles,reaction.rear,!!g.emptyCoupled,0,'trailer')];
  // Tractor x coordinates have their own datum; visualization maps it to the trailer datum.
  for(const a of axles)if(a.kind.startsWith('tractor'))a.sceneX=a.x-g.fifthWheelX+g.kingpinX;
 }
 return {available:true,axles,kingpinKg:g.emptyCoupled?null:kingpinKg,kingpinDeltaKg:g.emptyCoupled?kingpinKg:null,
         couplingLoadKnown:g.kind==='semitrailer'?!g.emptyCoupled:null,
         unstable:(g.kind==='semitrailer'&&!g.emptyCoupled&&(reaction.front<0||reaction.rear<0))||axles.some(a=>a.unstable),
         grossKg:axles.reduce((s,a)=>s+a.kg,0),method:g.emptyCoupled?'Измеренные нагрузки пустого автопоезда плюс реакции от груза':'Статическая модель двух опор; нагрузка внутри группы распределяется по указанным долям',assumption:g.distributionSource||'Доли нагрузки заданы пользователем'};
}
if(typeof module!=='undefined')module.exports={gearErrors,groupPosition,supportReactions,grossAxleLoads};
