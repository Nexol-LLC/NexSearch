const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict'),zlib=require('node:zlib');
const THREE=require('../vendor/three.min.js'),frames=new Map();let clock=0,frameId=0;
const tick=ms=>{clock=ms;const ready=[...frames.values()];frames.clear();for(const frame of ready)frame(ms)};
const context=vm.createContext({console,THREE,Blob,Response,DecompressionStream,atob,performance:{now:()=>clock},matchMedia:()=>({matches:false}),requestAnimationFrame:fn=>(frames.set(++frameId,fn),frameId),cancelAnimationFrame:id=>frames.delete(id),document:{documentElement:{dataset:{}},createElement:()=>({width:0,height:0,getContext:()=>({measureText:s=>({width:String(s).length*20}),fillText:()=>{}})})}});
for(const file of ['i18n.js','models.js','vehicle-layout.js','cargo-geometry.js','scene.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../src',file),'utf8'),context,{filename:file});
const result=vm.runInContext(`(()=>{
 _scene=new THREE.Scene();_vehGroup=new THREE.Group();_cargoGroup=new THREE.Group();_labelGroup=new THREE.Group();_cgGroup=new THREE.Group();_scene.add(_vehGroup,_cargoGroup,_labelGroup,_cgGroup);_orbit.target=new THREE.Vector3();_camera=new THREE.PerspectiveCamera();_renderer={render:()=>{}};
 const container={group:'container',id:'c20',L:6000,W:2400,H:2500,maxPayload:20000};
 const lowbed={group:'truck',id:'pdf-lowbed7',L:10650,W:2500,H:4000,maxPayload:60000,trailer:{loadingHeight:600}};
 const vehicles=[container,lowbed];sceneBuild(container,0,vehicles);
 const statics=_vehGroup.children[0],records=JSON.parse(JSON.stringify(_sceneRecords20));
 const item={name:'Ящик',weight:100,ci:0};const boxes=[0,1].map(truck=>({id:truck+1,x:0,y:0,z:0,l:500,w:500,h:500,item,zone:{type:'truck',truck,section:0}}));
 sceneBoxes(boxes,['#337799'],null);const positions=boxes.map(boxWorld);const zones=boxes.map(b=>zoneKeyOf(b.zone));
 const hit=zoneAt(positions[1].x,positions[1].z,boxes[1]);
 sceneBuild(lowbed,1,vehicles);const preserved=statics===_vehGroup.children[0];
 return {records,positions,zones,hitTruck:hit.truck,preserved,same:sameZone(boxes[0],boxes[1]),boxCount:_cargoGroup.children.filter(c=>c.isMesh).length,axles:(()=>{let n=0;_vehGroup.traverse(o=>{if(o.userData.axle)n++});return n})()};
})()`,context);
assert(result.records[1].minZ-result.records[0].maxZ>=4);
assert.equal(result.positions[1].x,result.positions[0].x);
assert(result.positions[1].z>result.positions[0].z+7);
assert.notEqual(result.zones[0],result.zones[1]);assert.equal(result.same,false);assert.equal(result.hitTruck,1);assert.equal(result.boxCount,2);assert.equal(result.axles,9);assert.equal(result.preserved,true);
assert(frames.size);tick(520);
const target=vm.runInContext('_orbit.target.x',context);assert(Math.abs(target-(result.records[1].minX+result.records[1].maxX)/2)<1e-9);
vm.runInContext('sceneOverview20(false)',context);const overview=vm.runInContext('_orbit.target.z',context);assert(Math.abs(overview-(result.records[0].minZ+result.records[1].maxZ)/2)<1e-9);
// Fit the complete world in a narrow viewport beside the open control panel.
const fit=vm.runInContext(`(()=>{_camera.aspect=.75;sceneOverview20(false);_camera.updateMatrixWorld();const points=[];for(const x of [_vehicleBounds.minX,_vehicleBounds.maxX])for(const y of [_vehicleBounds.minY,_vehicleBounds.maxY])for(const z of [_vehicleBounds.minZ-WH_GAP-WH_DEPTH/1000,_vehicleBounds.maxZ+WH_GAP+WH_DEPTH/1000]){const p=new THREE.Vector3(x,y,z).project(_camera);points.push([p.x,p.y,p.z])}return points})()`,context);
for(const point of fit){assert(Math.abs(point[0])<1);assert(Math.abs(point[1])<1);assert(point[2]>-1&&point[2]<1)}
// Replacement at the same index must animate to new bounds, without an upward arc.
vm.runInContext(`var vehicles20=[{group:'container',id:'short',L:6000,W:2400,H:2500},{group:'truck',id:'pdf-lowbed7',L:10650,W:2500,H:4000,trailer:{loadingHeight:600}}];sceneBuild(vehicles20[1],1,vehicles20);focusTruck20(1,false);var before20=_orbit.target.clone();vehicles20[1]={group:'container',id:'long',L:12000,W:2400,H:2600};sceneBuild(vehicles20[1],1,vehicles20);`,context);
assert(frames.size);const oldCallback=[...frames.values()][0];tick(780);
assert(vm.runInContext('_orbit.target.y<=before20.y&&_orbit.target.y>=_vehicleBounds.maxY/3',context));
const halfway=vm.runInContext('JSON.stringify(_orbit.target)',context);
vm.runInContext('sceneBuild(vehicles20[0],0,vehicles20)',context);oldCallback(1040);
assert.equal(vm.runInContext('JSON.stringify(_orbit.target)',context),halfway,'cancelled camera callback must not change the new flight');
tick(1300);assert.equal(frames.size,0);
assert(vm.runInContext('Math.abs(_orbit.target.z-(_sceneRecords20[0].minZ+_sceneRecords20[0].maxZ)/2)<1e-9',context));
// Metadata edits keep the camera; changing the lane cannot place cargo in an aisle.
const beforeEdit=vm.runInContext('JSON.stringify([_orbit.target,_orbit.dist])',context);
vm.runInContext('vehicles20[0].maxPayload=10000;sceneBuild(vehicles20[0],0,vehicles20)',context);
assert.equal(frames.size,0);assert.equal(vm.runInContext('JSON.stringify([_orbit.target,_orbit.dist])',context),beforeEdit);
assert.equal(vm.runInContext('zoneAt(1,5,{})',context),null,'aisle is not a warehouse');
assert.equal(vm.runInContext(`zoneAt(1,_zones.find(z=>z.kind==='wh'&&z.side==='R').z0+1,{}).side`,context),'R');
const wide=vm.runInContext(`projectSceneLayout([{group:'container',L:6000,W:2500,H:3000,oversize:{enabled:true,right:5000}},{group:'container',L:6000,W:2500,H:3000,oversize:{enabled:true,left:5000}}])`,context);
assert(wide[1].minZ-wide[0].maxZ>=4,'opposite oversize projections must leave an aisle');
// Centre of gravity marker must follow the selected lane, in local cargo mm.
vm.runInContext(`sceneBuild(vehicles20[1],1,vehicles20);sceneSetCg([{visible:true,manual:true,section:0,x:500,y:800,z:100}])`,context);
assert.equal(vm.runInContext('_cgGroup.children[0].position.z',context),8.8);
// Exercise the actual label renderer: positions follow the lane and identical
// values retain GPU resources instead of rebuilding every label on each edit.
const controls=fs.readFileSync(path.join(__dirname,'../src/controls14.js'),'utf8');
vm.runInContext(`var project={trucks:vehicles20.map(vehicle=>({vehicle})),cur:1,boxes:[]};_canvasEl={clientWidth:700,clientHeight:800};function computeStats(){return {}}function grossAxleLoads(){return {available:false}};vehicles20[1]={group:'truck',id:'pdf-lowbed7',L:10650,W:2500,H:4000,trailer:{loadingHeight:600}};project.trucks[1].vehicle=vehicles20[1];sceneBuild(vehicles20[1],1,vehicles20)`,context);
vm.runInContext(controls.slice(controls.indexOf('sceneSetAxleLoads=loadsBySec=>'),controls.indexOf('const renderPerspective14=')),context);
vm.runInContext('sceneSetAxleLoads({});var firstLabel20=_labelGroup.children[0];sceneSetAxleLoads({})',context);
assert.equal(vm.runInContext('_labelGroup.children.length',context),9);
assert.equal(vm.runInContext('firstLabel20===_labelGroup.children[0]',context),true);
assert.equal(vm.runInContext('firstLabel20.position.z',context),7.72);
vm.runInContext(`function $(id){return {classList:{toggle(){}}}}`,context);
vm.runInContext(controls.slice(controls.indexOf('const renderPerspective14=')),context);
vm.runInContext(`sceneView14('top');sceneOverview20(false);_camera.updateMatrixWorld()`,context);
const overhead=vm.runInContext(`(()=>{const points=[];for(const x of [_vehicleBounds.minX,_vehicleBounds.maxX])for(const z of [_vehicleBounds.minZ-WH_GAP-WH_DEPTH/1000,_vehicleBounds.maxZ+WH_GAP+WH_DEPTH/1000]){const p=new THREE.Vector3(x,0,z).project(_camera);points.push([p.x,p.y,p.z])}return points})()`,context);
for(const point of overhead){assert(Math.abs(point[0])<1);assert(Math.abs(point[1])<1);assert(point[2]>-1&&point[2]<1)}
vm.runInContext(`sceneView14('3d');focusTruck20(1,false)`,context);
const drag=vm.runInContext(`(()=>{const originalPlanePoint=planePoint,b=_boxes[0];_drag={box:b,plane:null,offX:0,offZ:0,zoneKey:zoneKeyOf(b.zone),lastX:b.x,lastY:b.y};planePoint=()=>new THREE.Vector3(1.25,0,9.25);try{moveDrag({});return {truck:b.zone.truck,x:b.x,y:b.y,parts:_cargoGroup.children.filter(o=>o.userData.boxId===b.id||o.userData.forBox===b.id).map(o=>[o.position.x,o.position.z])}}finally{planePoint=originalPlanePoint;_drag=null}})()`,context);
assert.equal(drag.truck,1);assert.equal(drag.x,1000);assert.equal(drag.y,1000);assert.equal(drag.parts.length,3);
for(const part of drag.parts){assert.equal(part[0],1.25);assert.equal(part[1],9.25)}
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/tractor-body20.js'),'utf8'),context);
const metadata=vm.runInContext('TRACTOR_BODY20',context),body=zlib.gunzipSync(Buffer.from(metadata.data,'base64'));
assert.equal(body.readUInt32LE(80),36783);assert.equal(body.length,84+36783*50);assert(metadata.min[0]>40);assert(metadata.max[1]>60);
console.log('Side-by-side lanes, oversize clearance, cargo picking, centre of gravity, smooth type changes, cancelled flights, viewport fit and original STL passed.');
