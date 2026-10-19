"""Exercise the actual Windows EXEs with a fresh, isolated local PostgreSQL."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import json,os,secrets,subprocess,sys,time
import requests

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from network import secure_store
from network.client import PinnedAdapter
from network.database import Database
from psycopg.conninfo import make_conninfo

def wait(url,process):
    for _ in range(180):
        if process.poll() is not None:raise RuntimeError('Compiled program exited before its interface became available')
        try:
            response=requests.get(url,timeout=1)
            if response.ok:return response
        except requests.RequestException:pass
        time.sleep(.3)
    raise RuntimeError('Compiled program startup timed out')

def stop(url,process):
    if not process or process.poll() is not None:return
    started=time.monotonic()
    try:
        info=requests.get(url+'/api/connection',timeout=2).json()
        requests.post(url+'/api/client/exit',headers={'Origin':url,'X-Nexol-CSRF':info['csrf']},json={},timeout=2)
    finally:process.wait(timeout=90)
    elapsed=time.monotonic()-started
    print('Graceful shutdown '+url+': '+str(round(elapsed,2))+' seconds',flush=True)
    assert elapsed<20,'Idle application shutdown must not wait for a streaming notification connection'

def main():
    # Keep every run independent: a prior company reservation belongs to its
    # previous employee and must never be reassigned to make a smoke test pass.
    base=ROOT/'preview/frozen-smoke16';home=base/('run-'+secrets.token_hex(4));home.mkdir(parents=True,exist_ok=True)
    host_home=home/'host';employee_home=home/'employee'
    host=employee=None
    trust_paths=[ROOT/'dist/HostWindows/client-trust.json',ROOT/'dist/EmployeeWindows/client-trust.json']
    originals={p:p.read_bytes() for p in trust_paths}
    env=os.environ.copy()
    for name in ('NEXOL_DATABASE_URL','CHECKO_API_KEY','NEXOL_RESOURCES','NEXOL_PROGRAM_DIR','NEXSEARCH_KEY_FILE','NEXSEARCH_SECRET_FILE'):env.pop(name,None)
    env.update(NEXOL_HOME=str(host_home),NEXOL_HOST_PORT='9057',NEXOL_UI_PORT='9058',NEXOL_POSTGRES_PORT='54369',
      NEXOL_POSTGRES_RUNTIME=str(ROOT/'preview/HostWindows/postgres'),NEXOL_NO_BROWSER='1',NEXSEARCH_NO_INSTALL='1',
      NEXSEARCH_UPDATE_STATE=str(home/'no-updates.json'))
    try:
        host=subprocess.Popen([str(ROOT/'dist/HostWindows/NexSearch.exe')],env=env,cwd=ROOT/'dist/HostWindows',creationflags=subprocess.CREATE_NO_WINDOW)
        wait('http://127.0.0.1:9058/api/connection',host)
        status=requests.get('http://127.0.0.1:9058/api/host/status',timeout=5);assert status.status_code==200,status.text
        assert status.json()['version']=='1.6.2'
        trust=json.loads((host_home/'identity/host-identity.json').read_text())
        session=requests.Session();session.trust_env=False;session.mount('https://',PinnedAdapter(trust['fingerprint']))
        secret=secure_store.read(host_home/'admin.sec')
        admin={'Authorization':'Bearer '+secret['token']}
        response=session.post('https://127.0.0.1:9057/api/auth/register',json={'first_name':'Демо','last_name':'Проверка','password':secrets.token_urlsafe(24),'device_id':'frozen-smoke16-'+secrets.token_hex(4),'computer_name':'Isolated smoke test'},timeout=5)
        assert response.status_code==200,response.text
        person=response.json();ident=person['employee']['id']
        approved=session.patch('https://127.0.0.1:9057/api/admin/employees/'+ident+'/approval',headers=admin,json={'approved':True},timeout=5)
        assert approved.status_code==200,approved.text
        person['employee']['approved']=True
        secure_store.write(employee_home/'employee.sec',{'host_url':'https://127.0.0.1:9057','device_id':'frozen-employee16','computer_name':'Isolated employee','token':person['token'],'profile':person['employee']})
        trust_paths[1].write_text(json.dumps(trust),encoding='utf-8')
        config=secure_store.read(host_home/'postgres.sec')
        dsn=make_conninfo(host='127.0.0.1',port=config['port'],user='nexol_app',password=config['app_password'],dbname='nexol')
        database=Database(dsn)
        item={'inn':'7700000062','name':'ДЕМО compiled 1.6','region':'Самарская область','source':'Учебный источник','source_url':'https://example.org/smoke16','phones':[],'emails':[],'verification':{'active':True,'checked_at':datetime.now(timezone.utc).isoformat()},'financial':{}}
        company,_,_=database.ingest(item,ident);database.close()
        employee_env={**env,'NEXOL_HOME':str(employee_home),'NEXOL_UI_PORT':'9060'}
        employee=subprocess.Popen([str(ROOT/'dist/EmployeeWindows/NexSearch.exe')],env=employee_env,cwd=ROOT/'dist/EmployeeWindows',creationflags=subprocess.CREATE_NO_WINDOW)
        wait('http://127.0.0.1:9060/api/connection',employee)
        ui='http://127.0.0.1:9060'
        index=requests.get(ui,timeout=5).text;assert '1.6.2' in index and 'workspace16.js' in index and 'mail_workspace16.js' in index and 'appearance.js' in index and 'customer_workspace.js' in index
        data=requests.get(ui+'/api/leads',timeout=5).json()['companies'];assert data[0]['company_time']['utc_offset']==4
        assert requests.get(ui+'/api/mail/accounts',timeout=5).json()['accounts']==[]
        card=requests.get(ui+'/api/companies/'+str(company),timeout=5);assert card.status_code==200 and card.json()['inn']==item['inn']
        assert requests.get(ui+'/api/companies/'+str(company)+'/interactions',timeout=5).status_code==200
        assert requests.get(ui+'/api/client/vpn/status',timeout=5).json()['phase']=='disabled'
        assert 'vpn16.js' in index
        assert requests.get(ui+'/assets/vpn16.js',timeout=5).status_code==200
        csrf=requests.get(ui+'/api/connection',timeout=5).json()['csrf'];headers={'Origin':ui,'X-Nexol-CSRF':csrf}
        if os.environ.get('NEXSEARCH_TEST_VPN')=='1':
            enabled=requests.post(ui+'/api/client/vpn/config',headers=headers,json={'enabled':True,'provider':'tailscale'},timeout=10)
            assert enabled.status_code==200,enabled.text
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                vpn=requests.get(ui+'/api/client/vpn/status',timeout=5).json()
                if vpn['phase']=='needs_login' and vpn.get('auth_url'):break
                assert vpn['phase'] not in ('missing','error'),vpn['message']
                time.sleep(.3)
            else:raise RuntimeError('The bundled native VPN helper did not reach first-login state')
            assert requests.post(ui+'/api/client/vpn/config',headers=headers,json={'enabled':False},timeout=20).status_code==200
        task=requests.post(ui+'/api/tasks',headers=headers,json={'title':'Compiled task 1.6','due_at':(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()},timeout=5)
        assert task.status_code==200,task.text
        assert requests.get(ui+'/api/notifications',timeout=5).json()['unread']>=1
        print('Compiled HostWindows and EmployeeWindows: startup, PostgreSQL migration, timezone assets, mail routes, tasks, notifications passed (VPN onboarding is opt-in).',flush=True)
        (base/'result.json').write_text(json.dumps({'version':'1.6.2','compiled_host':True,'compiled_employee':True,'schema':17,'passed':True,'test_home':str(home)}),encoding='utf-8')
    finally:
        try:stop('http://127.0.0.1:9060',employee)
        finally:
            stop('http://127.0.0.1:9058',host)
            for p,value in originals.items():p.write_bytes(value)

if __name__=='__main__':main()
