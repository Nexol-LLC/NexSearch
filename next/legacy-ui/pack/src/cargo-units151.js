/* UI conversions only. The solver and saved plans always use millimetres/kg. */
const CARGO_UNITS151={length:{mm:1,cm:10,m:1000},weight:{kg:1,t:1000}};
function cargoToBase151(value,kind,unit){
 if(value===''||value==null)return '';
 const factor=CARGO_UNITS151[kind]?.[unit],number=Number(value);
 if(!factor||!Number.isFinite(number))throw Error('Некорректная единица или значение');
 return Number((number*factor).toPrecision(15));
}
function cargoFromBase151(value,kind,unit){
 if(value===''||value==null)return '';
 const factor=CARGO_UNITS151[kind]?.[unit],number=Number(value);
 if(!factor||!Number.isFinite(number))throw Error('Некорректная единица или значение');
 return Number((number/factor).toPrecision(15));
}
if(typeof module!=='undefined')module.exports={cargoToBase151,cargoFromBase151,CARGO_UNITS151};
