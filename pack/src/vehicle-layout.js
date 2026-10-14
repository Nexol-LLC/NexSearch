/* Millimetres. Trailer coordinates use the front of the usable loading floor.
   PDF dimensions are transcribed, not inferred from payload or drawing pixels. */
const TRAILER_AXLE_LAYOUTS = {
 'pdf-jumba4': {deckL:11130, kingpinToEnd:13670, firstFromKingpin:6440, gaps:[2510,1360,1360], page:2},
 'pdf-megajumba4': {deckL:10899, kingpinToEnd:13583, firstFromKingpin:6383, gaps:[1352,1360,2509], page:3},
 'pdf-mega4': {deckL:11560, kingpinToEnd:14205, firstFromKingpin:5625, gaps:[2600,1310,1310], page:4},
 'pdf-lowbed5': {deckL:11800, kingpinToEnd:14625, firstFromKingpin:6875, gaps:[1360,1360,1360,1360], page:5},
 'pdf-lowbed7': {deckL:10650, kingpinToEnd:14660, firstFromKingpin:5230, gaps:[1310,1310,1310,1310,1310,1310], page:6},
 'pdf-beam': {deckL:15875, kingpinToEnd:14420, firstFromKingpin:5650, gaps:[2510,1810,1810,1810], page:7},
 'pdf-blade': {deckL:16410, kingpinToEnd:14940, firstFromKingpin:6160, gaps:[2510,1810,1810,1810], page:8},
 // Page 9: three front axle lines and five rear lines; 1500 mm spacing.
 // The connecting offsets and tractor coupling are schematic, not dimensioned.
 'pdf-modular': {deckL:9000, frontXs:[-4500,-3000,-1500], rearXs:[10250,11750,13250,14750,16250], page:9, schematic:true}
};
function trailerDrawingLayout(id,L){
 const spec=TRAILER_AXLE_LAYOUTS[id];if(!spec)return null;
 const extension=L-spec.deckL;let xs=[],kingpinX=null;
 if(spec.gaps){kingpinX=spec.deckL-spec.kingpinToEnd;let x=kingpinX+spec.firstFromKingpin+extension;xs.push(x);for(const gap of spec.gaps){x+=gap;xs.push(x)}}
 else xs=[...spec.frontXs,...spec.rearXs.map(x=>x+extension)];
 return {xs,kingpinX,schematic:!!spec.schematic,source:`Чертёж PDF · стр. ${spec.page}${spec.schematic?' · привязка модулей схематическая':''}`,page:spec.page};
}
function sectionAxleLayout(s){
 const isNum=x=>typeof x==='number'&&Number.isFinite(x),g=s.runningGear;
 const drawing=trailerDrawingLayout(s.id,s.L),semi=g?g.kind==='semitrailer':!!drawing||!!(s.axles&&s.axles.front>=0);
 const groundY=-(s.trailer?.loadingHeight||1250);
 const axis=(x,kind,i,id)=>({x,kind,id:id||kind+'-'+i,code:(kind.startsWith('tractor')?'Т':kind==='trailer'?'П':'А')+(i+1)});
 let trailer=[],tractor=[],rigid=[];
 let source=drawing?drawing.source+' · тягач типовой, уточните профиль':'Типовая схема · сверить с машиной';
 if(semi){
  const kingpin=isNum(g?.kingpinX)?g.kingpinX:isNum(drawing?.kingpinX)?drawing.kingpinX:s.axles?.front??-7500;
  const fifth=isNum(g?.fifthWheelX)?g.fifthWheelX:3400;
  const front=g?.tractorFront?.length?g.tractorFront:[{x:0}],rear=g?.tractorRear?.length?g.tractorRear:[{x:3600}];
  const raw=[...front.map((a,i)=>({...a,id:a.id||'tractor-front-'+i,kind:'tractor-front'})),...rear.map((a,i)=>({...a,id:a.id||'tractor-rear-'+i,kind:'tractor-rear'}))];
  tractor=raw.filter(a=>isNum(a.x)).sort((a,b)=>a.x-b.x).map((a,i)=>axis(a.x-fifth+kingpin,a.kind,i,a.id));
  const xs=drawing?.xs||Array.from({length:3},(_,i)=>(s.axles?.rear??s.L*.72)+(i-1)*1310);
  trailer=(g?.trailerAxles?.length?g.trailerAxles:xs.map(x=>({x}))).filter(a=>isNum(a.x)).sort((a,b)=>a.x-b.x).map((a,i)=>axis(a.x,'trailer',i,a.id));
 }else{
  const raw=g?[...(g.front||[]).map((a,i)=>({...a,id:a.id||'rigid-front-'+i,kind:'rigid-front'})),...(g.rear||[]).map((a,i)=>({...a,id:a.id||'rigid-rear-'+i,kind:'rigid-rear'}))]:[{x:s.axles?.front??-1200,kind:'rigid-front'},{x:s.axles?.rear??s.L*.72,kind:'rigid-rear'}];
  rigid=raw.filter(a=>isNum(a.x)).sort((a,b)=>a.x-b.x).map((a,i)=>axis(a.x,a.kind,i,a.id));
 }
 if(g)source='Профиль машины'+(g.geometryConfirmed===false?' · схема не подтверждена':'');
 const modules=s.id==='pdf-modular'?[{x:-5400,l:4500,z:800},{x:s.L+500,l:7500,z:800}]:[];
 const axles=[...tractor,...trailer,...rigid].map(a=>{
  const desired=a.kind==='trailer'?390:490;
  const outside=a.x+desired<0||a.x-desired>s.L;
  const upperFloor=outside?(modules.length?800:s.trailer?550:0):0;
  const radius=Math.min(desired,(upperFloor-groundY-130)/2);
  return {...a,radius,centerY:groundY+radius,wheelTop:groundY+2*radius,groundY};
 });
 return {kind:semi?'semitrailer':'rigid',axles,groundY,source,modules,trailerCount:trailer.length,tractorCount:tractor.length,schematic:!!drawing?.schematic};
}
if(typeof module!=='undefined')module.exports={TRAILER_AXLE_LAYOUTS,trailerDrawingLayout,sectionAxleLayout};
