const assert=require('node:assert/strict'),fs=require('fs'),vm=require('vm');
const c={module:{exports:{}},VEHICLE_CATALOG:[]};vm.createContext(c);
for(const file of ['models','vehicle-layout','trailer-data','running-gear','vehicle-profile15'])vm.runInContext(fs.readFileSync(require.resolve('../src/'+file+'.js'),'utf8'),c);
const plain=x=>JSON.parse(JSON.stringify(x));
const expected={
 'pdf-jumba4':[3900,6410,7770,9130],
 'pdf-megajumba4':[3699,5051,6411,8920],
 'pdf-mega4':[2980,5580,6890,8200],
 'pdf-lowbed5':[4050,5410,6770,8130,9490],
 'pdf-lowbed7':[1220,2530,3840,5150,6460,7770,9080],
 'pdf-beam':[7105,9615,11425,13235,15045],
 'pdf-blade':[7630,10140,11950,13760,15570],
 'pdf-modular':[-4500,-3000,-1500,10250,11750,13250,14750,16250]
};
for(const [id,positions] of Object.entries(expected)){
 const v=c.vehicleById(id),sec=c.getSections(v)[0],layout=c.sectionAxleLayout(sec);
 assert.equal(layout.trailerCount,positions.length);assert.equal(layout.tractorCount,2);
 assert.deepEqual(plain(layout.axles.filter(a=>a.kind==='trailer').map(a=>a.x)),positions);
 assert.equal(new Set(layout.axles.map(a=>a.code)).size,positions.length+2);
 for(const axle of layout.axles){
  assert.equal(axle.centerY-axle.radius,layout.groundY);
  if(axle.x>=0&&axle.x<=sec.L)assert(axle.wheelTop<=-130,'tire must clear usable floor and chassis: '+id);
 }
 assert.deepEqual(plain(c.sectionAxleLayout({...sec,W:3000,maxPayload:1000}).axles),plain(layout.axles),'changing width/payload must not change axle count/radius');
 const extended=c.trailerDrawingLayout(id,v.L+4000);
 assert.deepEqual(plain(extended.xs),positions.map(x=>id==='pdf-modular'&&x<0?x:x+4000),'extension moves trailer bogie, preserves axle spacing');
 if(id==='pdf-modular')continue;
 const defaults=c.defaultProfile15(v);assert.equal(defaults.geometryConfirmed,false);
 const gear=c.profileGear15({...defaults,originalSpacing:defaults.spacing,frontEmptyKg:4500,rearEmptyKg:5500,trailerEmptyKg:8000,geometryConfirmed:true});
 assert.deepEqual(plain(gear.trailerAxles.map(a=>a.x)),positions,'saving simplified profile must retain non-uniform PDF spacing');
 const loads=c.grossAxleLoads({runningGear:gear},{weight:20000,cgX:v.L/2});assert(loads.available);assert(Math.abs(loads.grossKg-38000)<1e-7);
 const same=c.profileGear15({...c.defaultProfile15({...v,runningGear:gear}),originalSpacing:defaults.spacing});
 assert.deepEqual(plain(same.trailerAxles),plain(gear.trailerAxles),'reopening/saving must preserve geometry and measured empty loads');
 const rendered=c.sectionAxleLayout({...sec,runningGear:gear});
 assert.equal(rendered.axles.length,loads.axles.length);
 for(const a of rendered.axles){const force=loads.axles.find(b=>b.kind===a.kind&&b.id===a.id);assert(force);assert.equal(a.x,force.sceneX??force.x)}
}
// A measured asymmetric profile must survive the simplified editor unchanged.
const base=c.defaultProfile15(c.vehicleById('pdf-jumba4'));
const gear=c.profileGear15({...base,originalSpacing:base.spacing,geometryConfirmed:true,frontEmptyKg:5000,rearEmptyKg:6000,trailerEmptyKg:7000});
gear.trailerAxles.forEach((a,i)=>{a.share=[.1,.2,.3,.4][i];a.emptyKg=[1000,1500,2000,2500][i]});
const reopen=c.defaultProfile15({runningGear:gear});
assert.deepEqual(plain(c.profileGear15({...reopen,originalSpacing:reopen.spacing}).trailerAxles),plain(gear.trailerAxles));
console.log('Trailer drawings: exact 4/5/7 axle positions, 3+5 modules, extensions, wheel clearance, render/physics alignment, asymmetric profile preservation passed.');
