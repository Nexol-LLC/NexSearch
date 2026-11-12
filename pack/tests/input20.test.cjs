const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const THREE=require('../vendor/three.min.js');
function target(){return {events:{},style:{},addEventListener(name,fn){this.events[name]=fn}}}
const canvas=target(),win=target();win.PointerEvent=function(){};let captured=null;
canvas.setPointerCapture=id=>{captured=id};canvas.hasPointerCapture=id=>captured===id;canvas.releasePointerCapture=()=>{captured=null;canvas.events.lostpointercapture?.()};
canvas.clientHeight=800;
const c=vm.createContext({THREE,window:win,console,canvas});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/scene.js'),'utf8'),c);
vm.runInContext(`var cameraMode14='top',switched20=0,moves20=0,ends20=0,hit20=null;_canvasEl=canvas;_orbit.target=new THREE.Vector3();_sceneActive20=0;
sceneRender=()=>{};pickCg=()=>null;pickBox=()=>hit20;selectBox=()=>{};sceneView14=mode=>{cameraMode14=mode;switched20++};startDrag=()=>{_drag={}};moveDrag=()=>moves20++;endDrag=()=>{_drag=null;ends20++};bindSceneInput20();`,c);
const event=(extra={})=>({pointerId:1,button:0,clientX:100,clientY:100,isPrimary:true,preventDefault(){},...extra});
canvas.events.pointerdown(event());assert.equal(captured,1);win.events.pointermove(event({clientX:200,clientY:120}));
assert.equal(vm.runInContext('cameraMode14',c),'3d');assert.equal(vm.runInContext('switched20',c),1);assert(vm.runInContext('_orbit.theta<-Math.PI/4',c));
win.events.pointerup(event());assert.equal(captured,null);const stopped=vm.runInContext('_orbit.theta',c);win.events.pointermove(event({clientX:300}));assert.equal(vm.runInContext('_orbit.theta',c),stopped);
vm.runInContext(`hit20={userData:{boxId:1}};_boxes=[{id:1,zone:{type:'truck',truck:0}}]`,c);
canvas.events.pointerdown(event({altKey:true}));win.events.pointermove(event({clientX:150}));assert.equal(vm.runInContext('moves20',c),0);win.events.blur();assert.equal(captured,null);
canvas.events.pointerdown(event());win.events.pointermove(event({clientX:140}));assert.equal(vm.runInContext('moves20',c),1);win.events.pointercancel(event());assert.equal(vm.runInContext('ends20',c),1);assert.equal(captured,null);
const before=vm.runInContext('_orbit.dist',c);canvas.events.wheel(event({deltaY:1,deltaMode:0}));const after=vm.runInContext('_orbit.dist',c);assert(after>before&&after<before*1.01,'touchpad ticks must not zoom by 10% each');
console.log('Real scene input handlers: pointer capture, rotation from 2D, Alt over cargo, cancellation, blur and smooth touchpad zoom passed.');
