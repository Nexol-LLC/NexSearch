const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict');
const context=vm.createContext({console,window:{addEventListener:()=>{}}});
for(const file of ['models.js','cargo-units151.js','network-plans.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../src',file),'utf8'),context,{filename:file});
const good={version:2,trucks:[{vehicle:{L:6000,W:2400,H:2500,maxPayload:null,runningGear:{kind:'rigid'}}},{vehicle:{L:13600,W:2450,H:2750,maxPayload:22000}}],cur:1,seq:3,cargo:[{name:'Постоянная погрузка',L:1000,W:1000,H:1000,weight:500,qty:2,rotatable:false}],boxes:[{id:1,itemIndex:0,x:100,y:100,z:0,l:1000,w:1000,h:1000,zone:{type:'truck',truck:0,section:0}},{id:2,itemIndex:0,x:200,y:100,z:0,l:1000,w:1000,h:1000,zone:{type:'truck',truck:1,section:0}}],settings:{cargo_units:{length:'cm',weight:'kg'},packing_mode:'balance',view_mode:'top',camera:{theta:1,phi:1,dist:25,target:[10,2,1]}}};
context.input=good;const restored=vm.runInContext('validatePlan20(input)',context);assert.equal(restored.trucks[0].vehicle.maxPayload,Infinity);assert.equal(good.trucks[0].vehicle.maxPayload,null);assert.deepEqual(JSON.parse(JSON.stringify(restored.settings)),good.settings);assert.equal(restored.cargo[0].rotatable,false);assert.equal(restored.boxes[1].zone.truck,1);
for(const mutate of [p=>p.boxes[1].id=1,p=>p.boxes[0].itemIndex=9,p=>p.boxes[0].zone.truck=5,p=>p.trucks[0].vehicle.W=0,p=>p.boxes[0].l=-1,p=>p.cargo[0].weight=-5]){const broken=structuredClone(good);mutate(broken);context.input=broken;assert.throws(()=>vm.runInContext('validatePlan20(input)',context));}
assert.equal(good.boxes[1].id,2);console.log('Named plan settings and multi-vehicle cargo survive validation; broken files are rejected before modifying the open project.');
context.input=good;vm.runInContext(`
let cargoDisplayUnits151={length:'m',weight:'t'},cameraMode14='3d',restores20=0,restored20=null,stored20=null;
const fields20={};function $(id){return fields20[id]||(fields20[id]={value:''})}
const _orbit={theta:0,phi:1,dist:20,target:{x:0,y:0,z:0,set(x,y,z){Object.assign(this,{x,y,z})}}};
const localStorage={setItem:(key,value)=>{stored20=JSON.parse(value)}};
function cargoUnitsKey151(){return 'units'}function rememberEdit(){}function restore14(data){restores20++;restored20=data}
function renderCargoCards15(){}function sceneView14(mode){cameraMode14=mode}function stopCameraFlight20(){}function sceneRender(){}
function snapshot14(){return JSON.stringify(restored20)}
restorePlan(input);
`,context);
assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(stored20)',context)),good.settings.cargo_units);
assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(planSnapshot().settings)',context)),good.settings);
const before=vm.runInContext('restores20',context);context.input={...structuredClone(good),boxes:[{...good.boxes[0],itemIndex:999}]};assert.throws(()=>vm.runInContext('restorePlan(input)',context));assert.equal(vm.runInContext('restores20',context),before);
