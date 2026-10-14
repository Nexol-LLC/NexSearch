const assert=require('assert');const fs=require('fs'),vm=require('vm');const context={module:{exports:{}}};vm.createContext(context);vm.runInContext(fs.readFileSync(require.resolve('../src/running-gear.js'),'utf8'),context);vm.runInContext(fs.readFileSync(require.resolve('../src/vehicle-profile15.js'),'utf8'),context);
const {profileGear15}=context.module.exports;
const input={kind:'semitrailer',rearCount:1,trailerCount:3,frontX:0,rearX:4000,kingpinX:0,fifthWheelX:3000,trailerX:8000,spacing:1340,frontEmptyKg:5000,rearEmptyKg:5000,trailerEmptyKg:4000,geometryConfirmed:true};
const gear=profileGear15(input),vehicle={runningGear:gear};
let result=context.grossAxleLoads(vehicle,{weight:16000,cgX:6000});assert(result.available);assert.equal(result.grossKg,30000);assert.equal(result.axles[0].kg,6000);assert(Math.abs(result.axles[1].kg-8000)<1e-8);assert(Math.abs(result.axles.slice(2).reduce((s,a)=>s+a.kg,0)-16000)<1e-9);
// Independent equilibrium: 16000 kg at 6m, supports 0m/8m -> 4t kingpin, 12t trailer.
assert(Math.abs(result.kingpinKg-4000)<1e-8);assert(Math.abs(result.axles.slice(2).reduce((s,a)=>s+a.kg,0)-16000)<1e-8);
for(const x of [1000,4000,7000]){result=context.grossAxleLoads(vehicle,{weight:16000,cgX:x});assert(Math.abs(result.grossKg-30000)<1e-8);const addedTrailer=result.axles.slice(2).reduce((s,a)=>s+a.kg,0)-4000;assert(Math.abs(addedTrailer*8000-16000*x)<1e-5)}
assert(!context.grossAxleLoads({runningGear:profileGear15({...input,trailerEmptyKg:null})},{weight:1000,cgX:1000}).available);
assert(!context.grossAxleLoads({runningGear:profileGear15({...input,trailerX:null})},{weight:1000,cgX:1000}).available);
const rigid=profileGear15({...input,kind:'rigid',frontEmptyKg:2000,rearEmptyKg:3000});result=context.grossAxleLoads({runningGear:rigid},{weight:10000,cgX:2000});assert.equal(result.grossKg,15000);assert.equal(result.axles[0].kg,7000);assert(Math.abs(result.axles[1].kg-8000)<1e-8);
console.log('Simple axle profiles: coupled tare, independent force/moment checks, missing inputs, rigid chassis passed.');
