const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {externalUrl}=require('../../../desktop/app/links.cjs');
const source=fs.readFileSync(path.join(__dirname,'../../web/app.js'),'utf8').split('\n').find(s=>s.startsWith('const safe='));
const safe=vm.runInNewContext(source+';safe',{URL});
for(const [input,expected] of [
 ['example.org/path','https://example.org/path'],['  www.example.org/  ','https://www.example.org/'],
 ['http://example.org/contacts','http://example.org/contacts'],['//example.org/a','https://example.org/a'],
 ['https://заводы.рф/','https://xn--80aegj1b5e.xn--p1ai/'],
 ['example.org:8080/a?x=1&amp;y=2','https://example.org:8080/a?x=1&y=2']
]){assert.equal(safe(input),expected,input);assert.equal(externalUrl(input),expected,input)}
for(const bad of ['javascript:alert(1)','file:///C:/Windows','mailto:a@example.org','data:text/html,x','https://a:b@example.org','https://example.org/\n','/relative','\\server\\file','']){assert.equal(safe(bad),'#',bad);assert.equal(externalUrl(bad),null,bad)}
console.log('Browser and desktop URL normalization, HTTP/HTTPS, IDN and unsafe URL rejection passed.');
