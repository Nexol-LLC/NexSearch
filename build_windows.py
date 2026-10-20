"""Clean builds: no host private identity, database or provider keys in binaries."""
from pathlib import Path
import argparse,subprocess,sys,shutil
ROOT=Path(__file__).resolve().parent
def build(kind):
 out=ROOT/'dist'/kind;out.mkdir(parents=True,exist_ok=True)
 entry={'EmployeeWindows':'employee_app.py','HostWindows':'host_app.py','Maintenance':'maintenance_app.py'}[kind]
 name='NexSearch Maintenance' if kind=='Maintenance' else 'NexSearch'
 command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile','--name',name,'--distpath',str(out),'--workpath',str(ROOT/'build'/kind),'--specpath',str(ROOT/'build')]
 if kind!='Maintenance':command+=['--windowed','--add-data',str(ROOT/'network/web')+';network/web','--add-data',str(ROOT/'network/client-trust.json')+';network','--add-data',str(ROOT/'version.json')+';.','--collect-all','zeroconf']
 if kind!='Maintenance':
  helper=ROOT/'network/bin/windows-amd64/nexsearch-vpn.exe'
  if not helper.is_file():raise RuntimeError('Build VPN first: python vpn/build.py windows-amd64')
  command+=['--add-binary',str(helper)+';network/bin/windows-amd64']
 command+=['--collect-data','certifi','--exclude-module','pytest','--exclude-module','numpy','--exclude-module','PIL']
 if kind=='HostWindows':
  for name2,dest in [('network/schema.sql','network'),('network/schema14.sql','network'),('network/schema15.sql','network'),('network/schema16.sql','network'),('network/schema17.sql','network'),('network/schema18.sql','network'),('network/schema19.sql','network'),('network/data','network/data'),('pack','pack'),('timezones.json','.')]:command+=['--add-data',str(ROOT/name2)+';'+dest]
  for module in ['uvicorn.logging','uvicorn.loops.asyncio','uvicorn.protocols.http.h11_impl','uvicorn.lifespan.on']:command+=['--hidden-import',module]
  for module in ['psycopg_binary','argon2','_argon2_cffi_bindings']:command+=['--collect-all',module]
 command.append(str(ROOT/entry));subprocess.run(command,cwd=ROOT,check=True)
 if kind!='Maintenance':
  shutil.copy2(ROOT/'network/client-trust.json',out/'client-trust.json');shutil.copy2(ROOT/'updates-public.pem',out/'updates-public.pem')
  shutil.copytree(ROOT/'desktop/app',out/'desktop/app',dirs_exist_ok=True)
  notices=out/'licenses';notices.mkdir(exist_ok=True)
  for source,name3 in [(ROOT/'vpn/TAILSCALE-LICENSE.txt','TAILSCALE-LICENSE.txt'),(ROOT/'network/bin/windows-amd64/THIRD-PARTY-NOTICES.txt','VPN-THIRD-PARTY-NOTICES.txt')]:
   destination=notices/name3
   if destination.exists():destination.chmod(0o644)
   destination.write_bytes(source.read_bytes())
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('kind',choices=['EmployeeWindows','HostWindows','Maintenance','all']);a=p.parse_args()
 for kind in ['EmployeeWindows','HostWindows','Maintenance'] if a.kind=='all' else [a.kind]:build(kind)
