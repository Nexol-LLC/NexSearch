/* Deterministic alternatives; each candidate is checked before it is offered. */
function planningBoxes(items,boxes){return boxes.map(b=>({...b,item:items[b.itemIndex??b.item?.ci],weight:items[b.itemIndex??b.item?.ci]?.weight||0,loadOnTop:0,below:null}))}
function unloadingProblems(boxes){const warnings=[];for(const a of boxes)for(const b of boxes){if(a===b||(a.item.stop||1)>=(b.item.stop||1))continue;const overlapY=a.y<b.y+b.w&&a.y+a.w>b.y,overlapZ=a.z<b.z+b.h&&a.z+a.h>b.z;const blocks=overlapY&&overlapZ&&b.x>=a.x+a.l||overlapY&&b.x<a.x+a.l&&b.x+b.l>a.x&&b.z>=a.z+a.h;if(blocks)warnings.push({earlier:a.item.name,blocking:b.item.name,earlierStop:a.item.stop||1,laterStop:b.item.stop||1})}return warnings}
function tryTargetShift(section,placed,fixed,target){if(!target||!placed.length)return placed;const st=computeStats(section,[...fixed,...placed]);const bounds=loadingBounds(section);let dx=target.x-st.cgX,dy=target.y-st.cgY;
 dx=Math.max(bounds.minX-Math.min(...placed.map(p=>p.x)),Math.min(bounds.maxX-Math.max(...placed.map(p=>p.x+p.l)),dx));dy=Math.max(bounds.minY-Math.min(...placed.map(p=>p.y)),Math.min(bounds.maxY-Math.max(...placed.map(p=>p.y+p.w)),dy));
 const shifted=placed.map(p=>({...p,x:Math.round(p.x+dx),y:Math.round(p.y+dy)}));
 if(shifted.every(p=>insideLoadingBounds(p,section)&&!hitsDeckObstacle(p,section)&&support(p,[...fixed,...shifted].filter(o=>o!==p),section).ratio>=.7&&!collides(p,fixed)))return shifted;
 return placed;
}
function candidatePlan(trucks,items,existing,strategy){
 let remaining=items.map((it,index)=>({...it,ci:index}));const boxes=[],failures=[];
 const fixed=existing.filter(b=>b.pinned&&remaining[b.itemIndex]);
 for(const b of fixed){remaining[b.itemIndex].qty--;boxes.push({...b,itemIndex:b.itemIndex})}
 remaining=remaining.filter(it=>it.qty>0);
 const metrics=[];
 for(let ti=0;ti<trucks.length;ti++){
  const truck=trucks[ti];
  for(const raw of getSections(truck.vehicle)){
   const section={...raw,packingStrategy:strategy};const bounds=loadingBounds(section);
   const localFixed=planningBoxes(items,fixed.filter(b=>b.zone.type==='truck'&&b.zone.truck===ti&&b.zone.section===section.index));
   const floors=floorSegments(section).map(f=>({...f,x:f.x-bounds.minX,y:f.y-bounds.minY}));
   const virtual={...section,L:bounds.maxX-bounds.minX,W:bounds.maxY-bounds.minY,H:bounds.H,floorSegments:section.floorSegments||bounds.oversize?floors:null,blockedRegions:(section.blockedRegions||[]).map(f=>({...f,x:f.x-bounds.minX,y:f.y-bounds.minY}))};
   const result=pack(virtual,remaining,localFixed.map(p=>({...p,x:p.x-bounds.minX,y:p.y-bounds.minY})));
   let placed=result.placed.map(p=>({...p,x:p.x+bounds.minX,y:p.y+bounds.minY,itemIndex:p.item.ci}));
   if(strategy==='balance')placed=tryTargetShift(section,placed,localFixed,truck.targetCg?.[section.index]||{x:section.L/2,y:section.W/2});
   const all=[...localFixed,...placed];const stats=computeStats(section,all),gear=grossAxleLoads(section,stats),unload=unloadingProblems(all);
   metrics.push({truck:ti,section:section.index,stats,gear,unloading:unload});
   for(const p of placed){const orientation=boxOrientation(p);boxes.push({itemIndex:p.itemIndex,x:p.x,y:p.y,z:p.z,l:p.l,w:p.w,h:p.h,orientation,orientationSigns:p.orientationSigns||(orientation[0]===1?[1,-1,1]:[1,1,1]),zone:{type:'truck',truck:ti,section:section.index},pinned:false})}
   remaining=result.unplaced.map(u=>({...u.item,qty:u.qty}));
  }
 }
 for(const it of remaining){const sections=trucks.flatMap(t=>getSections(t.vehicle));const fits=sections.some(s=>{const b=loadingBounds(s);return (it.verticalRotation?[[it.L,it.W,it.H],[it.W,it.L,it.H],[it.H,it.W,it.L],[it.L,it.H,it.W],[it.W,it.H,it.L],[it.H,it.L,it.W]]:it.rotatable?[[it.L,it.W,it.H],[it.W,it.L,it.H]]:[[it.L,it.W,it.H]]).some(([l,w,h])=>l<=b.maxX-b.minX&&w<=b.maxY-b.minY&&h<=b.H)});const reason=it.weight>Math.max(...trucks.map(t=>t.vehicle.maxPayload||0))?'Масса места превышает грузоподъёмность':!fits?'Габариты места превышают доступный отсек во всех разрешённых ориентациях':'Не найдено размещение: остаток грузоподъёмности, свободная площадь, опора или ярусы. Проверьте закреплённые места и попробуйте другой вариант';failures.push({name:it.name,itemIndex:it.ci,qty:it.qty,reason})}
 const over=metrics.flatMap(m=>m.gear.axles||[]).filter(a=>a.over||a.unstable).length;
 const unloading=metrics.reduce((s,m)=>s+m.unloading.length,0),unplaced=remaining.reduce((s,it)=>s+it.qty,0);
 return {strategy,boxes,unplaced:failures,metrics,placed:boxes.filter(b=>b.zone.type==='truck').length,overloadedAxles:over,unloadingConflicts:unloading};
}
function alternativePlans(payload){return ['capacity','balance','unloading'].map(strategy=>candidatePlan(payload.trucks,payload.items,payload.fixed||[],strategy))}
if(typeof module!=='undefined')module.exports={unloadingProblems};
