import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

// Exercise the actual UI adapter, including async account changes. No live data.
const source=readFileSync(new URL('../legacy-ui/assets/workspace20.js',import.meta.url),'utf8');
const head=source.slice(0,source.indexOf('const enterBefore20='));
const appearance=source.slice(source.indexOf('const appearanceBefore20='),source.indexOf('\n\nconst settingsBefore20='));
function setup(invoke){
 const calls=[],styles=new Map();
 const context=vm.createContext({user:{id:'one'},services:{tracking:{name:'Tracking'}},window:{__TAURI__:{core:{invoke}}},document:{documentElement:{style:{setProperty:(k,v)=>styles.set(k,v)}}},appearance162:{theme:'light',density:'comfortable',motion:true},applyAppearance162:p=>{context.appearance162=p},structuredClone,setTimeout:fn=>{calls.push(fn);return 1},clearTimeout:()=>{},asyncError16:e=>{throw e}});
 vm.runInContext(head+appearance,context);
 return {context,calls,styles,run:s=>vm.runInContext(s,context)};
}
const workspace=()=>({preferences:{employee_id:'one',theme:'dark',density:'compact',accent:[10,20,30],reduced_motion:true,services:[{id:'saved',name:'Maps',url:'https://example.org'}]},profile:{industries:[],regions:[],routes:[],available_capacity:30}});
test('restores native appearance and personal links for the signed-in employee',async()=>{
 const s=setup(async()=>({workspace:workspace()}));await s.run('loadPersonal20()');
 assert.equal(s.context.appearance162.theme,'dark');assert.equal(s.context.appearance162.density,'compact');assert.equal(s.context.appearance162.motion,false);
 assert.equal(s.context.services['personal:saved'].name,'Maps');assert.equal(s.styles.get('--accent'),'rgb(10,20,30)');
});
test('drops personal settings when the account changes during loading',async()=>{
 let release;const s=setup(()=>new Promise(r=>{release=r}));const pending=s.run('loadPersonal20()');s.context.user={id:'two'};release({workspace:workspace()});await pending;
 assert.equal(s.run('personal20'),null);assert.equal(s.context.services['personal:saved'],undefined);
});
test('saves the normalized theme, density and animation preference',async()=>{
 let saved;const s=setup(async(cmd,args)=>{if(cmd==='workspace_save')saved=args.workspace;else return {workspace:workspace()}});
 await s.run('loadPersonal20()');s.run("applyAppearance162({theme:'light',density:'comfortable',motion:true},true)");await s.calls.at(-1)();
 assert.equal(saved.preferences.theme,'light');assert.equal(saved.preferences.density,'comfortable');assert.equal(saved.preferences.reduced_motion,false);
});
