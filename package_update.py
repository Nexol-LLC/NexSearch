"""Build deterministic, Ed25519 signed source or compiled delta packages."""
from pathlib import Path
import sys,os,json,base64,zipfile,hashlib,argparse,subprocess
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
ROOT=Path(__file__).resolve().parent
IGNORED={'.git','.updates','.github','.runtime','identity','__pycache__','.pytest_cache','build','dist','preview','research','tests'}
def source_files():
 for p in ROOT.rglob('*'):
  rel=p.relative_to(ROOT)
  if not p.is_file() or any(x in IGNORED for x in rel.parts) or p.suffix in ('.sec','.enc','.pyc','.db','.dump','.zip','.gz','.env'):continue
  if p.suffix=='.pem' and p.name!='updates-public.pem':continue
  yield p,rel.as_posix()
def package(role,output,key,commit):
 version=json.loads((ROOT/'version.json').read_text())['version'];files={}
 if role=='EmployeeWindows':
  for p in (ROOT/'dist/EmployeeWindows').rglob('*'):
   if p.is_file():files[p.relative_to(ROOT/'dist/EmployeeWindows').as_posix()]=p.read_bytes()
  files['NexSearch Maintenance.exe']=(ROOT/'dist/Maintenance/NexSearch Maintenance.exe').read_bytes()
 elif role in ('EmployeeMac','HostUbuntu'):
  for p,name in source_files():files[name]=p.read_bytes()
  files['client-trust.json']=(ROOT/'network/client-trust.json').read_bytes()
 else:raise ValueError(role)
 metadata={'version':version,'source_commit':commit,'role':role,'schema':15}
 files['package-info.json']=json.dumps(metadata,indent=2).encode()
 manifest={**metadata,'files':{role+'/'+name:hashlib.sha256(data).hexdigest() for name,data in sorted(files.items())}}
 raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode();output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
  archive.writestr('release.json',raw);archive.writestr('release.sig',key.sign(raw))
  for name,data in sorted(files.items()):archive.writestr(role+'/'+name,data)
 print(json.dumps({'role':role,'version':version,'files':len(files),'size':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('role',choices=['HostUbuntu','EmployeeWindows','EmployeeMac']);p.add_argument('--output',required=True);p.add_argument('--commit');a=p.parse_args()
 key=Ed25519PrivateKey.from_private_bytes(base64.b64decode(os.environ['NEXSEARCH_SIGNING_KEY']))
 commit=a.commit or subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip();package(a.role,a.output,key,commit)
