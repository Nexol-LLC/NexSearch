const assert=require('assert');const {grossAxleLoads}=require('../src/running-gear.js');
const axle=(x,emptyKg=0,share=1,limitKg=10000)=>({x,emptyKg,share,limitKg});
const rigid={runningGear:{kind:'rigid',front:[axle(0,2000)],rear:[axle(6000,3000)]}};
let result=grossAxleLoads(rigid,{weight:12000,cgX:3000});assert(result.available);assert.equal(result.axles[0].kg,8000);assert.equal(result.axles[1].kg,9000);assert.equal(result.grossKg,17000);
result=grossAxleLoads(rigid,{weight:12000,cgX:8000});assert(result.unstable);assert(result.axles[1].over);
assert(!grossAxleLoads({},{}).available);assert(!grossAxleLoads({runningGear:{...rigid.runningGear,front:[axle(0,null)]}},{weight:1,cgX:0}).available);
const trailer={runningGear:{kind:'semitrailer',kingpinX:0,trailerTareKg:6000,trailerTareCgX:6000,fifthWheelX:3000,
 tractorFront:[axle(0,5000)],tractorRear:[axle(4000,3000)],trailerAxles:[axle(7500,0,.5),axle(8500,0,.5)]}};
result=grossAxleLoads(trailer,{weight:16000,cgX:6000});assert(result.available);assert.equal(result.kingpinKg,5500);assert.equal(result.grossKg,30000);assert.equal(result.axles[2].kg,8250);assert.equal(result.axles[3].kg,8250);assert.equal(result.axles[0].kg,6375);assert.equal(result.axles[1].kg,7125);
trailer.runningGear.trailerAxles[0].share=.2;assert(!grossAxleLoads(trailer,{weight:16000,cgX:6000}).available);
console.log('Running gear: mass balance, moments, tare, individual axle shares, incomplete data and overloads passed.');
