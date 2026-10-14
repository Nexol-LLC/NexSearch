"""Root-only scheduled fetch of signed artifacts; the app holds no Git credentials."""
from pathlib import Path
import os,sys,json,subprocess,hashlib,shutil,tempfile,time,ssl,urllib.request,fcntl
BASE=Path('/opt/nexsearch');CACHE=Path('/var/lib/nexsearch-updater');PUBLIC=Path('/etc/nexsearch/updates-public.pem');PACKAGES=Path('/var/lib/nexsearch-updates')
os.environ['GIT_ASKPASS']='/etc/nexsearch/github-askpass'
os.environ['GIT_TERMINAL_PROMPT']='0'
def run(*args,**kwargs):return subprocess.run(args,check=True,**kwargs)
def atomic(path,value):
 temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2));temp.chmod(0o640);shutil.chown(temp,group='nexsearch');temp.replace(path)
def audit(kind,details):
 payload=json.dumps(details)
 code="import psycopg,json,sys; from psycopg.types.json import Jsonb; c=psycopg.connect('dbname=nexsearch'); c.execute(\"INSERT INTO audit_events(kind,entity_id,details) VALUES(%s,'',%s)\",(sys.argv[1],Jsonb(json.loads(sys.stdin.read())))); c.commit()"
 run('runuser','-u','postgres','--',str(BASE/'venv/bin/python'),'-c',code,kind,input=payload,text=True)
def read_package(branch,name,destination):
 data=subprocess.check_output(['git','--git-dir',str(CACHE/'repo.git'),'show','refs/remotes/origin/'+branch+':.updates/'+name])
 destination.write_bytes(data);return hashlib.sha256(data).hexdigest()
def verified(archive,role):
 # Bootstrap verifier is installed by the operator and refreshed after a signed deployment.
 sys.path.insert(0,str(BASE/'control'))
 from network.updates import prepare
 return prepare(archive,Path('/etc/nexsearch'),CACHE,role)
def healthy():
 from cryptography import x509
 from cryptography.hazmat.primitives import hashes
 trust=json.loads((BASE/'current/network/client-trust.json').read_text())
 context=ssl._create_unverified_context()
 import http.client
 for _ in range(60):
  try:
   c=http.client.HTTPSConnection('127.0.0.1',8743,context=context,timeout=2);c.connect()
   fingerprint=x509.load_der_x509_certificate(c.sock.getpeercert(True)).fingerprint(hashes.SHA256()).hex()
   if fingerprint!=trust['fingerprint'].replace(':','').lower():raise ValueError('Host certificate differs')
   c.request('GET','/api/host-info');r=c.getresponse();body=json.loads(r.read());c.close()
   if r.status==200 and body['host_id']==trust['host_id']:return True
  except Exception:pass
  time.sleep(1)
 return False
def host_install(archive,digest,state):
 prepared=verified(archive,'HostUbuntu');stage=Path(prepared['state_file']).parent;meta=json.loads((stage/'package-info.json').read_text())
 state['host_available']=meta;current=BASE/'current';old=current.resolve()
 if (old/'package-info.json').is_file() and json.loads((old/'package-info.json').read_text()).get('source_commit')==meta['source_commit']:
  shutil.rmtree(stage);state.pop('host_deferred',None);return
 # Never migrate an active search mid-run; Persistent timer retries on next check.
 code="import psycopg; c=psycopg.connect('dbname=nexsearch'); print(c.execute(\"SELECT count(*) FROM jobs WHERE status IN ('queued','running')\").fetchone()[0])"
 count=int(subprocess.check_output(['runuser','-u','postgres','--',str(BASE/'venv/bin/python'),'-c',code],text=True))
 if count:state['host_deferred']='Есть активные поиски';shutil.rmtree(stage);return
 run('runuser','-u','postgres','--',str(BASE/'venv/bin/python'),str(old/'ubuntu/backup_pg.py'))
 target=BASE/'releases'/('github-'+digest[:20])
 if not target.exists():shutil.copytree(stage,target,ignore=shutil.ignore_patterns('release.json','release.sig','update-state.json'))
 target.chmod(0o755)
 for entry in target.rglob('*'):entry.chmod(0o755 if entry.is_dir() else 0o644)
 shutil.rmtree(stage)
 # Dependencies and all privileged migrations come only from signed packages.
 run(str(BASE/'venv/bin/pip'),'install','-r',str(target/'network/requirements.txt'),stdout=subprocess.DEVNULL)
 run('systemctl','stop','nexsearch')
 try:
  migration="import psycopg,sys; from pathlib import Path; c=psycopg.connect('dbname=nexsearch'); c.execute('SET ROLE nexsearch_owner'); c.execute(\"SELECT set_config('nexol.is_admin','true',true)\"); [c.execute(Path(p).read_text()) for p in sys.argv[1:]]; c.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO nexsearch_app'); c.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO nexsearch_app'); c.execute('REVOKE UPDATE,DELETE ON audit_events,call_records FROM nexsearch_app'); c.commit()"
  run('runuser','-u','postgres','--',str(BASE/'venv/bin/python'),'-c',migration,str(target/'network/schema14.sql'),str(target/'network/schema15.sql'))
  pending=BASE/'current.new';pending.unlink(missing_ok=True);pending.symlink_to(target);pending.replace(current)
  run('systemctl','start','nexsearch')
  if not healthy():raise RuntimeError('Updated host did not pass health check')
  shutil.copy2(target/'ubuntu/updater.py',BASE/'control/updater.py');(BASE/'control/updater.py').chmod(0o755)
  (BASE/'control/network').mkdir(exist_ok=True);(BASE/'control/network/__init__.py').touch();shutil.copy2(target/'network/updates.py',BASE/'control/network/updates.py')
  state.pop('host_deferred',None);state['host_installed']={**meta,'installed_at':time.time()};audit('update.host_installed',meta)
 except Exception:
  pending=BASE/'current.new';pending.unlink(missing_ok=True);pending.symlink_to(old);pending.replace(current);run('systemctl','restart','nexsearch');audit('update.host_rollback',meta);raise
def main():
 CACHE.mkdir(parents=True,exist_ok=True);lock=(CACHE/'update.lock').open('w')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:return
 PACKAGES.mkdir(parents=True,exist_ok=True);(PACKAGES/'packages').mkdir(exist_ok=True)
 path=PACKAGES/'status.json'
 try:state=json.loads(path.read_text())
 except (OSError,ValueError):state={'packages':{}}
 from datetime import datetime,timezone
 state.update(checked_at=datetime.now(timezone.utc).isoformat(),schedule='10:00 Europe/Moscow',message='Проверяем GitHub')
 try:
  repo=CACHE/'repo.git'
  if not repo.exists():run('git','init','--bare',str(repo));run('git','--git-dir',str(repo),'remote','add','origin','https://github.com/Nexol-LLC/NexSearch.git')
  run('git','--git-dir',str(repo),'fetch','--depth=1','origin','+refs/heads/host:refs/remotes/origin/host','+refs/heads/employe:refs/remotes/origin/employe',stdout=subprocess.DEVNULL)
  for role,name in [('EmployeeWindows','employee-windows.zip'),('EmployeeMac','employee-mac.zip')]:
   archive=CACHE/name
   try:digest=read_package('employe',name,archive)
   except subprocess.CalledProcessError:continue
   prepared=verified(archive,role);stage=Path(prepared['state_file']).parent;meta=json.loads((stage/'package-info.json').read_text());target=PACKAGES/'packages'/(digest+'.zip')
   if not target.exists():shutil.copy2(archive,target);target.chmod(0o640);shutil.chown(target,group='nexsearch')
   state['packages'][role]={**meta,'sha256':digest,'size_bytes':target.stat().st_size};shutil.rmtree(stage)
  archive=CACHE/'host-linux.zip'
  try:digest=read_package('host','host-linux.zip',archive)
  except subprocess.CalledProcessError:digest=None
  if digest:host_install(archive,digest,state)
  state['message']='Проверка завершена';state.pop('error',None);audit('update.checked',{'branches':['host','employe'],'packages':list(state['packages'])})
 except Exception as error:state.update(message='Обновление не завершено; рабочая версия сохранена',error=type(error).__name__);audit('update.failed',{'error':type(error).__name__});raise
 finally:atomic(path,state)
if __name__=='__main__':main()
