"""Build deterministic, Ed25519 signed source or compiled delta packages."""
from pathlib import Path
import sys,os,json,base64,zipfile,hashlib,argparse,subprocess,time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
ROOT=Path(__file__).resolve().parent
IGNORED={'.git','.updates','.github','.runtime','identity','__pycache__','.pytest_cache','build','dist','preview','research','tests','company-collector','.venv','node_modules','deployment'}
def source_files():
 for p in ROOT.rglob('*'):
  rel=p.relative_to(ROOT)
  if not p.is_file() or any(x in IGNORED for x in rel.parts) or p.name.startswith('.env') or p.suffix in ('.sec','.enc','.pyc','.db','.sqlite','.sqlite3','.dump','.zip','.gz','.env'):continue
  if p.suffix=='.pem' and p.name!='updates-public.pem':continue
  yield p,rel.as_posix()
def package(role,output,key,commit,trust_file=None):
 version_info=json.loads((ROOT/'version.json').read_text());version=version_info['version'];files={}
 if role=='EmployeeWindows':
  for p in (ROOT/'dist/EmployeeWindows').rglob('*'):
   if p.is_file():files[p.relative_to(ROOT/'dist/EmployeeWindows').as_posix()]=p.read_bytes()
  files['NexSearch Maintenance.exe']=(ROOT/'dist/Maintenance/NexSearch Maintenance.exe').read_bytes()
 elif role in ('EmployeeMac','HostUbuntu'):
  for p,name in source_files():
   if name.startswith('network/bin/') and not name.startswith('network/bin/'+('darwin-' if role=='EmployeeMac' else 'linux-')):continue
   files[name]=p.read_bytes()
  files['client-trust.json']=(ROOT/'network/client-trust.json').read_bytes()
 else:raise ValueError(role)
 metadata={'version':version,'source_commit':commit,'role':role,'schema':version_info['schema'],'built_at':int(time.time())}
 public_profile=ROOT/'deployment/internet-access.json'
 if public_profile.is_file():
  from network.internet_access import validate_profile
  trust=json.loads((ROOT/'network/client-trust.json').read_text(encoding='utf-8'))
  configured=validate_profile(json.loads(public_profile.read_text(encoding='utf-8')),trust)
  files['internet-access.json']=json.dumps(configured,sort_keys=True).encode()
  if role in ('HostUbuntu','EmployeeMac'):files['network/internet-access.json']=files['internet-access.json']
 if trust_file is not None:
  if role not in ('EmployeeWindows','EmployeeMac'):raise ValueError('A host override is only valid for employee packages')
  trust=json.loads(Path(trust_file).read_text(encoding='utf-8'))
  from uuid import UUID
  UUID(trust['host_id'])
  if set(trust)!={'host_id','fingerprint'} or len(trust['fingerprint'])!=64 or any(c not in '0123456789abcdef' for c in trust['fingerprint']):raise ValueError('Expected a public host identity with a SHA256 fingerprint')
  raw_trust=json.dumps(trust,sort_keys=True).encode()
  files['client-trust.json']=raw_trust
  if role=='EmployeeMac':files['network/client-trust.json']=raw_trust
  metadata['host_id']=trust['host_id']
  if 'internet-access.json' in files and json.loads(files['internet-access.json'])['host_id']!=trust['host_id']:
   files.pop('internet-access.json',None);files.pop('network/internet-access.json',None)
 files['package-info.json']=json.dumps(metadata,indent=2).encode()
 manifest={**metadata,'files':{role+'/'+name:hashlib.sha256(data).hexdigest() for name,data in sorted(files.items())}}
 raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode();output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
  archive.writestr('release.json',raw);archive.writestr('release.sig',key.sign(raw))
  for name,data in sorted(files.items()):archive.writestr(role+'/'+name,data)
 print(json.dumps({'role':role,'version':version,'files':len(files),'size':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('role',choices=['HostUbuntu','EmployeeWindows','EmployeeMac']);p.add_argument('--output',required=True);p.add_argument('--commit');p.add_argument('--trust',type=Path);a=p.parse_args()
 key=Ed25519PrivateKey.from_private_bytes(base64.b64decode(os.environ['NEXSEARCH_SIGNING_KEY']))
 commit=a.commit or subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip();package(a.role,a.output,key,commit,a.trust)
