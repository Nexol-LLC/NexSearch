"""Daily signed employee downloads over pinned authenticated host transport."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import os,sys,json,threading,time,hashlib,subprocess,shutil,xml.etree.ElementTree as ET
from . import secure_store
from .version import VERSION
MOSCOW=timezone(timedelta(hours=3))
def clock():return datetime.now(MOSCOW)
def state_path(home):return Path(home)/'updates/daily.json'
def read_state(home):
 try:return json.loads(state_path(home).read_text(encoding='utf-8'))
 except (OSError,ValueError):return {}
def write_state(home,state):
 path=state_path(home);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix('.tmp');temp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
def role():return 'EmployeeMac' if sys.platform=='darwin' else 'EmployeeWindows'
def check_update(program,home,trust,agent=None,force=False):
 from .client import PinnedAdapter
 import requests
 home=Path(home);program=Path(program);state=read_state(home);now=clock()
 if not force and (now.hour<10 or state.get('date')==now.date().isoformat()):return state
 if agent:
  with agent.lock:url=agent.url;token=agent.state.get('token')
  session=agent.session
 else:
  profile=secure_store.read(home/'employee.sec',{});url=profile.get('host_url');token=profile.get('token');session=requests.Session();session.trust_env=False;session.mount('https://',PinnedAdapter(trust['fingerprint']))
 if not token or not url:return state
 state.update(checked_at=now.isoformat(),message='Проверяем ветку employe',branch='employe');write_state(home,state)
 try:
  headers={'Authorization':'Bearer '+token};response=session.get(url+'/api/update/info',params={'platform':role()},headers=headers,timeout=30);response.raise_for_status();info=response.json()
  if not info.get('available'):state.update(message='Новый комплект ещё не опубликован');write_state(home,state);return state
  state.update(version=info['version'],source_commit=info['source_commit'])
  try:installed=json.loads((program/'package-info.json').read_text())
  except (OSError,ValueError):installed={}
  if state.get('downloaded_sha256')==info['sha256'] or state.get('installed_commit')==info['source_commit'] or installed.get('source_commit')==info['source_commit']:
   state.update(date=now.date().isoformat(),message='Актуальная версия; обновлений нет');write_state(home,state);return state
  archive=home/'updates'/('employee-'+info['sha256']+'.zip');temporary=archive.with_suffix('.part');digest=hashlib.sha256();size=0
  with session.get(url+'/api/update/package',params={'platform':role(),'sha256':info['sha256']},headers=headers,stream=True,timeout=(10,120)) as download:
   download.raise_for_status()
   with temporary.open('wb') as file:
    for chunk in download.iter_content(1024*1024):
     if not chunk:continue
     size+=len(chunk)
     if size>256*1024*1024:raise ValueError('Комплект слишком большой')
     digest.update(chunk);file.write(chunk)
  if size!=info['size_bytes'] or digest.hexdigest()!=info['sha256']:raise ValueError('Контрольная сумма комплекта не совпала')
  temporary.replace(archive)
  from .updates import prepare
  prepared=prepare(archive,program,home,role())
  state.update(date=now.date().isoformat(),downloaded_sha256=info['sha256'],pending_state=prepared['state_file'],pending_commit=info['source_commit'],message='Обновление проверено; установится при закрытии программы')
 except Exception as error:state['message']='Проверка не завершена: '+type(error).__name__;state['last_error']=type(error).__name__
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
 program=Path(program);home=Path(home);folder=home/'updates';runner=folder/('runner-'+str(int(time.time()))+'.exe')
 if sys.platform=='win32':
  shutil.copy2(program/'NexSearch Maintenance.exe',runner)
  subprocess.Popen([str(runner),'apply','--state',pending,'--home',str(home)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS)
 elif sys.platform=='darwin':
  subprocess.Popen([sys.executable,'-m','network.daily_updates','--apply',pending,'--home',str(home),'--wait-pid',str(os.getpid())],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
def record_applied(home):
 state=read_state(home);state['installed_commit']=state.get('pending_commit');state.pop('pending_state',None);state['message']='Обновление установлено';write_state(home,state)
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
  apply(args.apply);record_applied(args.home)
 else:
  trust=json.loads((Path(args.program)/'client-trust.json').read_text(encoding='utf-8'));check_update(args.program,args.home,trust)
if __name__=='__main__':main()
