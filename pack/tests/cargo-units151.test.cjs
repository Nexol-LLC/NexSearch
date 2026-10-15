const assert=require('node:assert/strict');
const {cargoToBase151:toBase,cargoFromBase151:fromBase}=require('../src/cargo-units151.js');
for(const [unit,input] of [['mm',13600],['cm',1360],['m',13.6]])assert.equal(toBase(input,'length',unit),13600);
for(const [unit,input] of [['kg',1250],['t',1.25]])assert.equal(toBase(input,'weight',unit),1250);
for(const kind of ['length','weight'])for(const unit of kind==='length'?['mm','cm','m']:['kg','t']){
 for(const value of [0,.001,2.75,1234.5,13600,1234567])assert(Math.abs(toBase(fromBase(value,kind,unit),kind,unit)-value)<1e-8);
 assert.equal(toBase('',kind,unit),'');assert.equal(fromBase('',kind,unit),'');
}
assert.throws(()=>toBase(1,'length','km'));assert.throws(()=>toBase(Infinity,'weight','t'));
console.log('Cargo UI conversions: all length/mass units, decimals, round trips and empty values passed.');
