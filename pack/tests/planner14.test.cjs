const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert');const context=vm.createContext({console});
for(const file of ['models.js','cargo-geometry.js','axles.js','running-gear.js','packer.js','planner14.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../src',file),'utf8'),context,{filename:file});
const result=vm.runInContext(`
 const v={L:6000,W:2400,H:2600,maxPayload:20000,floorSegments:[{x:0,y:0,l:2000,w:2400,z:500},{x:2000,y:0,l:4000,w:2400,z:0}]};
 const cargo=[makeCargoItem({name:'Груз A',L:1000,W:1000,H:1000,weight:1000,qty:5,stop:1}),makeCargoItem({name:'Груз B',L:1000,W:1000,H:1000,weight:2000,qty:5,stop:2,cg:[.2,.3,.4]})];
 const alternatives=alternativePlans({trucks:[{vehicle:v,targetCg:{0:{x:4000,y:1200}}}],items:cargo,fixed:[]});
 const errors=[];for(const plan of alternatives){const boxes=planningBoxes(cargo,plan.boxes);for(const p of boxes){if(!insideLoadingBounds(p,v)||hitsDeckObstacle(p,v)||support(p,boxes.filter(o=>o!==p),v).ratio<.7)errors.push(plan.strategy+' unsupported');}}
 const vertical=pack({L:1500,W:1200,H:1500,maxPayload:10000},[makeCargoItem({name:'Вертикально',L:2000,W:500,H:500,qty:1,weight:10,verticalRotation:true})]);
 ({alternatives:alternatives.map(p=>({strategy:p.strategy,count:p.placed,conflicts:p.unloadingConflicts,positions:p.boxes.map(b=>[b.x,b.y,b.z,b.l,b.w]),unplaced:p.unplaced})),errors,vertical:vertical.placed.length});
`,context);
assert.equal(result.alternatives.length,3);assert(!result.errors.length,result.errors);assert(result.alternatives.every(p=>p.count===10),JSON.stringify(result.alternatives));
assert.equal(result.vertical,0,'All orientations remain too large for the stated bounding rectangle');
console.log('Alternative packing plans preserve boundaries, stepped-deck support and delivery metadata.');
