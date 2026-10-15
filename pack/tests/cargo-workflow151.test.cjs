const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict');
const context=vm.createContext({console});
for(const file of ['cargo-units151.js','models.js','cargo-geometry.js','axles.js','running-gear.js','packer.js','planner14.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../src',file),'utf8'),context,{filename:file});
const result=vm.runInContext(`(()=>{
 const out=[];
 for(const [unit,size] of [['m',1],['cm',100],['mm',1000]])for(const [massUnit,mass] of [['t',.1],['kg',100]])for(const tiers of [1,2,3]){
  const vehicle={L:1000,W:1000,H:3100,maxPayload:10000};
  const item=makeCargoItem({name:'Проверка ярусов',L:cargoToBase151(size,'length',unit),W:1000,H:1000,weight:cargoToBase151(mass,'weight',massUnit),qty:4,tiers});
  const plans=alternativePlans({trucks:[{vehicle}],items:[item],fixed:[]});
  out.push({unit,massUnit,tiers,plans:plans.map(p=>({placed:p.placed,unplaced:p.unplaced.reduce((n,r)=>n+r.qty,0),maxZ:Math.max(...p.boxes.map(b=>b.z)),weight:p.boxes.length*item.weight}))});
 }
 return out;
})()`,context);
for(const row of result)for(const plan of row.plans){
 assert.equal(plan.placed,row.tiers,JSON.stringify(row));assert.equal(plan.unplaced,4-row.tiers);
 assert.equal(plan.maxZ,(row.tiers-1)*1000);assert.equal(plan.weight,row.tiers*100);
}
console.log('Cargo units and stacking: six unit combinations, 1/2/3 tiers and three solver strategies passed.');
