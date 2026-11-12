const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict');
const context=vm.createContext({console});
for(const file of ['models.js','cargo-geometry.js','axles.js','packer.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../src',file),'utf8'),context,{filename:file});
vm.runInContext(`
const vehicle={L:1000,W:1800,H:1000,maxPayload:10000};
const base={name:'Только после поворота',L:1600,W:800,H:500,weight:50,qty:1};
for(const value of [undefined,null,false,0,'0','false','']){
 const item=makeCargoItem({...base,rotatable:value});
 if(item.rotatable||pack(vehicle,[item],[]).placed.length)throw Error('Поворот должен быть выключен: '+value);
}
for(const value of [true,1,'1','true']){
 const item=makeCargoItem({...base,rotatable:value});
 if(!item.rotatable||pack(vehicle,[item],[]).placed.length!==1)throw Error('Явно разрешённый поворот потерян: '+value);
 const restored=makeCargoItem(JSON.parse(JSON.stringify(item)));
 if(!restored.rotatable)throw Error('Сохранённый выбор поворота потерян');
}
`,context);
// Exercise the real CSV importer, including absent, empty and explicit values.
const imported=[];
context.FileReader=class{readAsArrayBuffer(){this.onload({target:{result:new ArrayBuffer(0)}})}};
context.XLSX={read:()=>({SheetNames:['Груз'],Sheets:{'Груз':{}}}),utils:{sheet_to_json:()=>[
 ['Название','Д','Ш','В','Вес','Количество','Ярусы','Поворот'],
 ['Без столбца',1600,800,500,50],['Пусто',1600,800,500,50,1,1,''],
 ['Разрешено',1600,800,500,50,1,1,1],['Запрещено',1600,800,500,50,1,1,0],
]}};
context.importDone=items=>imported.push(...items);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/io.js'),'utf8'),context,{filename:'io.js'});
vm.runInContext('importFile({},importDone,error=>{throw error})',context);
assert.deepEqual(imported.map(item=>item.rotatable),[false,false,true,false]);
console.log('Default rotation, real solver orientations, saved preferences and Excel/CSV import passed.');
