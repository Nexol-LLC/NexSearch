from pathlib import Path
import plistlib
from network.mac_entry import native_schedule

def test_mac_migration_preserves_daily_schedule_but_replaces_obsolete_python_session_reader(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    monkeypatch.setattr('os.getuid',lambda:501,raising=False)
    calls=[]
    monkeypatch.setattr('subprocess.run',lambda command,**kw:calls.append(command))
    host='00000000-0000-4000-8000-000000000001';label='com.nexsearch.updates.'+host
    agent=tmp_path/'Library/LaunchAgents'/(label+'.plist');agent.parent.mkdir(parents=True)
    old={'Label':label,'ProgramArguments':['old-python','-m','network.daily_updates'],'StartCalendarInterval':{'Hour':10,'Minute':0},'StandardErrorPath':'private.log'}
    with agent.open('wb') as f:plistlib.dump(old,f)
    base=tmp_path/'Программа с пробелом';home=tmp_path/'Данные'
    native_schedule(base,home,host)
    with agent.open('rb') as f:new=plistlib.load(f)
    assert new['ProgramArguments']==[str(base/'NexSearch Maintenance'),'check-daily','--program',str(base),'--home',str(home)]
    assert new['StartCalendarInterval']==old['StartCalendarInterval'] and new['StandardErrorPath']=='private.log'
    assert len(calls)==2 and calls[1][-1]==str(agent)
    native_schedule(base,home,host)
    assert len(calls)==2
