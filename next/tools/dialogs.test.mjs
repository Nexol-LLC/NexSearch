import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const app=readFileSync(new URL('../../network/web/app.js',import.meta.url),'utf8');
const lifecycle=app.slice(app.indexOf('function openEditor('),app.indexOf('\nfunction financialHtml('));
assert.ok(lifecycle.length>100&&lifecycle.length<5000,'test must execute the actual editor lifecycle');
const extensions=readFileSync(new URL('../../network/web/workspace16.js',import.meta.url),'utf8');
const revisionHooks=extensions.slice(extensions.indexOf('const oldOpen16='),extensions.indexOf('\n',extensions.indexOf('const previousClose16=')));
const settings=readFileSync(new URL('../../network/web/settings_hosted.js',import.meta.url),'utf8');

class Element extends EventTarget {
 constructor(){super();this.open=false;this.classList={remove(){}};this.textContent='';this.hidden=false;this.disabled=false}
 showModal(){this.open=true}
 close(){this.open=false;this.dispatchEvent(new Event('close'))}
}
function setup(){
 const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id)},cleared=[];
 const context=vm.createContext({$:get,window:{NexDesktop:{hide(){}}},user:{id:'employee'},view:'pack',dialogOpen:false,editing:null,editorRevision16:0,companyCard16:null,mountService(){},load:async()=>{},lastVersion:'',notice(){},clearInterval:id=>cleared.push(id)});
 vm.runInContext(lifecycle+revisionHooks,context);
 // The real settings close hook must stop polling and invalidate in-flight responses.
 vm.runInContext(settings.slice(settings.indexOf('let hostSettingsTimer='),settings.indexOf('\nfunction settingsVisible')),context);
 return {context,get,cleared,run:code=>vm.runInContext(code,context)};
}
for(const id of ['#close-editor','#cancel-editor'])test(`${id} closes with current feature cleanup and no save`,()=>{
 const s=setup();s.run("openEditor('Настройки хоста','',()=>{throw Error('must not save')});hostSettingsTimer=42");
 s.get(id).dispatchEvent(new Event('click'));
 assert.equal(s.get('#editor').open,false);assert.equal(s.run('editing'),null);assert.equal(s.run('dialogOpen'),false);
 assert.equal(s.run('editorRevision16'),2);assert.deepEqual(s.cleared,[42]);assert.equal(s.run('settingsEpoch'),1);
});
test('Escape closes through the same revision and timer cleanup',()=>{
 const s=setup();s.run("openEditor('Настройки','',null);hostSettingsTimer=7");
 const cancel=new Event('cancel',{cancelable:true});s.get('#editor').dispatchEvent(cancel);
 assert.equal(cancel.defaultPrevented,true);assert.equal(s.get('#editor').open,false);
 assert.equal(s.run('editorRevision16'),2);assert.deepEqual(s.cleared,[7]);
});
test('finishing an old save cannot close or reload a newly opened settings window',async()=>{
 const s=setup();let release,reloads=0;s.context.pending=()=>new Promise(r=>{release=r});s.context.load=async()=>{reloads++};
 s.run("openEditor('Старая карточка','',pending)");
 const save=s.get('#editor-form').onsubmit({preventDefault(){}});
 s.get('#cancel-editor').dispatchEvent(new Event('click'));
 s.run("openEditor('Настройки хоста','',null)");release();await save;
 assert.equal(s.get('#editor').open,true);assert.equal(s.get('#editor-title').textContent,'Настройки хоста');assert.equal(reloads,0);
});
test('a delayed native close event does not clear a reopened editor',()=>{
 const s=setup();s.run("openEditor('Первое','',null);closeEditor();openEditor('Второе','',()=>{})");
 s.get('#editor').dispatchEvent(new Event('close'));
 assert.equal(s.run('dialogOpen'),true);assert.equal(typeof s.run('editing'),'function');
});
