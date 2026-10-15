"""Daily signed employee downloads over pinned authenticated host transport."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import os,sys,json,threading,time,hashlib,subprocess,shutil,xml.etree.ElementTree as ET
from contextlib import contextmanager
from uuid import uuid4
from . import secure_store
from .version import VERSION
MOSCOW=timezone(timedelta(hours=3))
def clock():return datetime.now(MOSCOW)
def state_path(home):return Path(home)/'updates/daily.json'
def read_state(home):
 try:return json.loads(state_path(home).read_text(encoding='utf-8'))
 except (OSError,ValueError):return {}
def write_state(home,state):
 path=state_path(home);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name('daily-'+uuid4().hex+'.tmp');temp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)

@contextmanager
def update_lock(home):
 """Serialize the scheduled helper and the running client, including downloads."""
 path=Path(home)/'updates/check.lock';path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('a+b') as file:
  file.seek(0);file.write(b'0');file.flush();file.seek(0)
  try:
   if sys.platform=='win32':
    import msvcrt
    msvcrt.locking(file.fileno(),msvcrt.LK_NBLCK,1)
   else:
    import fcntl
    fcntl.flock(file,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except OSError:yield False;return
  try:yield True
  finally:
   file.seek(0)
   if sys.platform=='win32':msvcrt.locking(file.fileno(),msvcrt.LK_UNLCK,1)
   else:fcntl.flock(file,fcntl.LOCK_UN)

def local_status(program,home):
 state=read_state(home)
 try:installed=json.loads((Path(program)/'package-info.json').read_text(encoding='utf-8'))
 except (OSError,ValueError):installed={}
 state.update(installed_version=installed.get('version',VERSION),installed_commit=installed.get('source_commit'))
 with update_lock(home) as free:state['checking']=not free
 return state
def role():return 'EmployeeMac' if sys.platform=='darwin' else 'EmployeeWindows'

def older_package(installed,incoming):
 def version(item):return tuple(int(part) for part in item.get('version','0').split('.'))
 if version(incoming)!=version(installed):return version(incoming)<version(installed)
 # New signed packages carry a build time, so an old cached build of the
 # same version cannot replace a manually installed corrected release.
 return bool(installed.get('built_at') and incoming.get('built_at',0)<installed['built_at'])
def check_update(program,home,trust,agent=None,force=False):
 with update_lock(home) as free:
  if not free:return read_state(home)
  return _check_update(program,home,trust,agent,force)

def _check_update(program,home,trust,agent=None,force=False):
 from .client import PinnedAdapter
 import requests
 home=Path(home);program=Path(program);state=read_state(home);now=clock()
 if not force:
  if now.hour<10 or state.get('date')==now.date().isoformat():return state
  try:
   if now<datetime.fromisoformat(state['retry_at']):return state
  except (KeyError,ValueError,TypeError):pass
 if agent:
  with agent.lock:url=agent.url;token=agent.state.get('token')
  session=agent.session
 else:
  profile=secure_store.read(home/'employee.sec',{});url=profile.get('host_url');token=profile.get('token');session=requests.Session();session.trust_env=False;session.mount('https://',PinnedAdapter(trust['fingerprint']))
 if not token or not url:
  state.update(phase='waiting',message='Для проверки обновления требуется подключение к хосту и вход');write_state(home,state);return state
 state.pop('last_error',None)
 state.update(checked_at=now.isoformat(),phase='checking',message='Проверяем пакет сотрудника на хосте',branch='employe',retry_at=(now+timedelta(minutes=5)).isoformat());write_state(home,state)
 try:
  headers={'Authorization':'Bearer '+token};response=session.get(url+'/api/update/info',params={'platform':role()},headers=headers,timeout=30);response.raise_for_status();info=response.json()
  state['host_checked_at']=info.get('checked_at')
  # At 10:00 the client can reach the host before its GitHub check finishes.
  # A stale cache must not suppress every further attempt for the whole day.
  try:host_fresh=datetime.fromisoformat(info['checked_at']).astimezone(MOSCOW)>=now.replace(hour=10,minute=0,second=0,microsecond=0)
  except (KeyError,ValueError,TypeError):host_fresh=False
  if host_fresh:state['date']=now.date().isoformat()
  else:state.pop('date',None)
  if not info.get('available'):state.update(phase='waiting',message='Хост ещё не подготовил пакет сотрудника; повторим проверку через 5 минут');state.pop('date',None);write_state(home,state);return state
  state.update(version=info['version'],source_commit=info['source_commit'])
  try:installed=json.loads((program/'package-info.json').read_text())
  except (OSError,ValueError):installed={}
  if installed.get('source_commit')==info['source_commit']:
   state.update(phase='current',message='Установлена актуальная сборка' if host_fresh else 'Установлена сборка из кеша хоста; ждём сегодняшней проверки хоста')
   state.pop('pending_state',None);state.pop('pending_commit',None);write_state(home,state);return state
  rejected=state.get('rejected_sha256')==info['sha256'] and state.get('rejected_installed_commit')==installed.get('source_commit')
  if older_package({'version':installed.get('version','0')},{'version':info['version']}) or rejected:
   if state.get('pending_commit')==info['source_commit']:state.pop('pending_state',None);state.pop('pending_commit',None)
   state.update(phase='waiting',message='У сотрудника установлена более новая сборка. Ждём обновления пакета на хосте');write_state(home,state);return state
  if state.get('pending_commit')==info['source_commit'] and Path(state.get('pending_state') or '').is_file():
   pending=json.loads(Path(state['pending_state']).read_text(encoding='utf-8'))
   candidate=json.loads((Path(pending['stage'])/'release.json').read_text(encoding='utf-8'))
   if not older_package(installed,candidate):
    state.update(phase='ready',message='Обновление готово. Полностью закройте программу и откройте её после установки');write_state(home,state);return state
   state.pop('pending_state',None);state.pop('pending_commit',None)
  if not isinstance(info['sha256'],str) or len(info['sha256'])!=64 or any(c not in '0123456789abcdef' for c in info['sha256']):raise ValueError('Некорректная контрольная сумма пакета')
  archive=home/'updates'/('employee-'+info['sha256']+'.zip');temporary=archive.with_suffix('.part')
  cached=archive.is_file() and archive.stat().st_size==info['size_bytes'] and hashlib.sha256(archive.read_bytes()).hexdigest()==info['sha256']
  if not cached:
   digest=hashlib.sha256();size=0
   state.update(phase='downloading',downloaded_bytes=0,total_bytes=info['size_bytes'],message='Скачиваем обновление с хоста');write_state(home,state)
   with session.get(url+'/api/update/package',params={'platform':role(),'sha256':info['sha256']},headers=headers,stream=True,timeout=(10,120)) as download:
    download.raise_for_status()
    with temporary.open('wb') as file:
     for chunk in download.iter_content(1024*1024):
      if not chunk:continue
      size+=len(chunk)
      if size>256*1024*1024:raise ValueError('Комплект слишком большой')
      digest.update(chunk);file.write(chunk)
      state['downloaded_bytes']=size;write_state(home,state)
   if size!=info['size_bytes'] or digest.hexdigest()!=info['sha256']:raise ValueError('Контрольная сумма комплекта не совпала')
   temporary.replace(archive)
  state.update(phase='verifying',message='Проверяем подпись и готовим установку');write_state(home,state)
  import zipfile
  from cryptography.hazmat.primitives.serialization import load_pem_public_key
  with zipfile.ZipFile(archive) as packet:
   raw=packet.read('release.json');load_pem_public_key((program/'updates-public.pem').read_bytes()).verify(packet.read('release.sig'),raw);candidate=json.loads(raw)
  if candidate.get('source_commit')!=info['source_commit'] or candidate.get('version')!=info['version'] or candidate.get('role')!=role():raise ValueError('Метаданные пакета не совпали')
  if older_package(installed,candidate):
   state.update(phase='waiting',rejected_sha256=info['sha256'],rejected_installed_commit=installed.get('source_commit'),message='У сотрудника установлена более новая сборка. Ждём обновления пакета на хосте');write_state(home,state);return state
  from .updates import prepare
  prepared=prepare(archive,program,home,role())
  state.update(phase='ready',downloaded_sha256=info['sha256'],pending_state=prepared['state_file'],pending_commit=info['source_commit'],message='Обновление готово. Полностью закройте программу и откройте её после установки')
 except Exception as error:
  state.pop('date',None);state.update(phase='error',message='Не удалось подготовить обновление. Повторите проверку; при повторной ошибке передайте код администратору',last_error=type(error).__name__)
 write_state(home,state);return state
class DailyUpdater:
 def __init__(self,agent,program):
  self.agent=agent;self.program=Path(program);self.closed=threading.Event();self.gate=threading.Lock();self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
 def check(self,force=False):
  if not self.gate.acquire(False):return
  try:check_update(self.program,self.agent.home,self.agent.trust,self.agent,force)
  finally:self.gate.release()
 def run(self):
  while not self.closed.wait(30):self.check()
 def close(self):self.closed.set();self.thread.join(timeout=5)
def install_schedule(program,home,trust):
 if os.environ.get('NEXSEARCH_NO_INSTALL') or not getattr(sys,'frozen',False) and sys.platform!='darwin':return
 program=Path(program);home=Path(home);folder=home/'updates';folder.mkdir(parents=True,exist_ok=True)
 if sys.platform=='win32':
  helper=program/'NexSearch Maintenance.exe'
  if not helper.exists():return
  task='NexSearchEmployeeUpdates-'+trust['host_id'];xml=folder/'schedule.xml';root=ET.Element('Task',version='1.2',xmlns='http://schemas.microsoft.com/windows/2004/02/mit/task')
  triggers=ET.SubElement(root,'Triggers');trigger=ET.SubElement(triggers,'CalendarTrigger');ET.SubElement(trigger,'StartBoundary').text=clock().date().isoformat()+'T07:00:00Z';ET.SubElement(trigger,'Enabled').text='true';days=ET.SubElement(trigger,'ScheduleByDay');ET.SubElement(days,'DaysInterval').text='1'
  principal=ET.SubElement(ET.SubElement(root,'Principals'),'Principal',id='Author');ET.SubElement(principal,'UserId').text=os.environ.get('USERDOMAIN','')+'\\'+os.environ.get('USERNAME','');ET.SubElement(principal,'LogonType').text='InteractiveToken';ET.SubElement(principal,'RunLevel').text='LeastPrivilege'
  settings=ET.SubElement(root,'Settings')
  for key,value in {'StartWhenAvailable':'true','DisallowStartIfOnBatteries':'false','StopIfGoingOnBatteries':'false','ExecutionTimeLimit':'PT20M','MultipleInstancesPolicy':'IgnoreNew'}.items():ET.SubElement(settings,key).text=value
  execute=ET.SubElement(ET.SubElement(root,'Actions',Context='Author'),'Exec');ET.SubElement(execute,'Command').text=str(helper);ET.SubElement(execute,'Arguments').text=subprocess.list2cmdline(['check-daily','--program',str(program),'--home',str(home),'--role','EmployeeWindows']);ET.SubElement(execute,'WorkingDirectory').text=str(program);ET.ElementTree(root).write(xml,encoding='utf-16',xml_declaration=True)
  result=subprocess.run(['schtasks.exe','/Create','/TN',task,'/XML',str(xml),'/F'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
  state=read_state(home);state['schedule_registered']=result.returncode==0;write_state(home,state)
 elif sys.platform=='darwin':
  import plistlib
  label='com.nexsearch.updates.'+trust['host_id'];agent_dir=Path.home()/'Library/LaunchAgents';agent_dir.mkdir(exist_ok=True)
  data={'Label':label,'ProgramArguments':[sys.executable,'-m','network.daily_updates','--program',str(program),'--home',str(home)],'StartInterval':60,'RunAtLoad':True,'EnvironmentVariables':{'PYTHONPATH':os.environ.get('PYTHONPATH',''),'PYTHONDONTWRITEBYTECODE':'1'},'StandardOutPath':str(folder/'scheduler.log'),'StandardErrorPath':str(folder/'scheduler.log')}
  file=agent_dir/(label+'.plist');file.write_bytes(plistlib.dumps(data));subprocess.run(['launchctl','bootstrap','gui/'+str(os.getuid()),str(file)],capture_output=True)
def apply_pending_on_exit(program,home):
 if os.environ.get('NEXSEARCH_NO_INSTALL'):return
 state=read_state(home);pending=state.get('pending_state')
 if not pending or not Path(pending).is_file():return
 try:
  installed=json.loads((Path(program)/'package-info.json').read_text(encoding='utf-8'))
  prepared=json.loads(Path(pending).read_text(encoding='utf-8'))
  candidate=json.loads((Path(prepared['stage'])/'release.json').read_text(encoding='utf-8'))
 except (OSError,ValueError,KeyError):return
 if installed.get('source_commit')==candidate.get('source_commit') or older_package(installed,candidate):
  state.pop('pending_state',None);state.pop('pending_commit',None);state.update(phase='current',message='Старый пакет не установлен; сохранена текущая сборка');write_state(home,state);return
 program=Path(program);home=Path(home);folder=home/'updates';runner=folder/('runner-'+str(int(time.time()))+'.exe')
 if sys.platform=='win32':
  shutil.copy2(program/'NexSearch Maintenance.exe',runner)
  subprocess.Popen([str(runner),'apply','--state',pending,'--home',str(home)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS)
 elif sys.platform=='darwin':
  subprocess.Popen([sys.executable,'-m','network.daily_updates','--apply',pending,'--home',str(home),'--wait-pid',str(os.getpid())],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
def record_applied(home,state_file):
 # Read the package actually applied, rather than a mutable newer pending value.
 prepared=json.loads(Path(state_file).read_text(encoding='utf-8'))
 manifest=json.loads((Path(prepared['stage'])/'release.json').read_text(encoding='utf-8'))
 state=read_state(home);state['installed_commit']=manifest.get('source_commit')
 pending_is_older=False
 if state.get('pending_state') and state.get('pending_state')!=str(state_file):
  try:
   pending=json.loads(Path(state['pending_state']).read_text(encoding='utf-8'))
   pending_is_older=datetime.fromisoformat(pending['verified_at'])<=datetime.fromisoformat(prepared['verified_at'])
  except (OSError,ValueError,KeyError,TypeError):pending_is_older=not Path(state['pending_state']).is_file()
 if state.get('pending_state')==str(state_file) or state.get('pending_commit')==manifest.get('source_commit') or pending_is_older or not state.get('pending_state'):
  state.pop('pending_state',None);state.pop('pending_commit',None);state.update(phase='installed',message='Обновление установлено')
 write_state(home,state)

def record_apply_error(home,error):
 state=read_state(home);state.update(phase='error',last_error=type(error).__name__,message='Установка не завершена. Полностью закройте программу и повторите установку');write_state(home,state)
def main():
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--program');parser.add_argument('--home',required=True);parser.add_argument('--apply');parser.add_argument('--wait-pid',type=int);args=parser.parse_args()
 if args.apply:
  if args.wait_pid:
   for _ in range(120):
    try:os.kill(args.wait_pid,0)
    except OSError:break
    time.sleep(1)
   else:raise RuntimeError('Employee process is still active')
  from .updates import apply
  try:apply(args.apply);record_applied(args.home,args.apply)
  except Exception as error:record_apply_error(args.home,error);raise
 else:
  trust=json.loads((Path(args.program)/'client-trust.json').read_text(encoding='utf-8'));check_update(args.program,args.home,trust)
if __name__=='__main__':main()
