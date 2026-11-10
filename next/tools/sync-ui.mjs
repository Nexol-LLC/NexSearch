// Developer tool only. The shipped application has no Node.js dependency.
import {execFileSync} from 'node:child_process';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {resolve,dirname,relative} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const next=resolve(dirname(fileURLToPath(import.meta.url)),'..'),root=resolve(next,'..'),out=resolve(next,'legacy-ui');
const commit=execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim();
const paths=execFileSync('git',['ls-tree','-r','--name-only','HEAD','--','network/web','pack/index.html','pack/src','pack/vendor'],{cwd:root,encoding:'utf8'}).trim().split('\n');
const files=[];
for(const source of paths){
 const bytes=readFileSync(resolve(root,source));
 const destination=source==='network/web/index.html'?'index.html':source.startsWith('network/web/')?'assets/'+source.slice('network/web/'.length):source;
 let content=bytes;
 if(destination==='index.html') content=Buffer.from(bytes.toString('utf8').replace('<script src="/assets/server_access.js">','<script src="/assets/rust_bridge.js"></script><script src="/assets/server_access.js">'));
 const target=resolve(out,destination);mkdirSync(dirname(target),{recursive:true});writeFileSync(target,content);
 files.push({source,destination,source_sha256:createHash('sha256').update(bytes).digest('hex'),sha256:createHash('sha256').update(content).digest('hex'),adaptation:destination==='index.html'?'Rust bridge script inserted before original scripts':null});
}
writeFileSync(resolve(next,'ui-source.json'),JSON.stringify({baseline_version:'1.6.7',reference_commit:commit,source:'current working tree; confirmed mail fixes included',files},null,2)+'\n');
console.log(`Перенесено файлов интерфейса и погрузки: ${files.length}. CSS и порядок исходных скриптов сохранены.`);
