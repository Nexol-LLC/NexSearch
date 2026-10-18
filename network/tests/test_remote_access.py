"""No external enrollment: exercise the supervisor, trust and access boundaries."""
from pathlib import Path
from types import SimpleNamespace
import json,subprocess,sys,threading,time
import pytest,requests
from network import remote_access as vpn
from network.client import EmployeeAgent,start_client
from network import secure_store
from test_crm import db,pg,http,header
from test_v16 import staff

TRUST={'host_id':'abc12345-1234-4567-8901-abcdefabcdef','fingerprint':'ab'*32}

@pytest.mark.parametrize('value',[
 {'enabled':'yes'}, {'enabled':True,'auth_key':'private'},
 {'enabled':True,'provider':'unknown'},
 *[{'enabled':True,'provider':'headscale','control_url':url} for url in
   ('http://vpn.example.org','https://user:pass@vpn.example.org','https://vpn.example.org?key=secret',
    'https://vpn.example.org/#private','https://vpn.example.org/path','https://vpn.example.org:wrong')]])
def test_invalid_settings_rejected(value):
    with pytest.raises(ValueError):vpn.validate_config(value)

def test_provider_and_status_boundaries(tmp_path):
    found=[];remote=vpn.RemoteAccess(tmp_path,TRUST,on_remote=found.append)
    assert remote.status()['phase']=='disabled'
    assert remote.status()['host_name']=='nexsearch-abc1234512344567'
    assert vpn.validate_config({'enabled':True,'control_url':'https://evil.example'})['control_url']==vpn.CONTROL
    remote.config['enabled']=True
    remote._event({'phase':'needs_login','auth_url':'https://evil.example/auth?secret=123','secret':'private'})
    assert 'auth_url' not in remote.status() and 'secret' not in remote.status()
    remote._event({'phase':'needs_login','auth_url':'https://login.tailscale.com/a/test'})
    assert remote.status()['auth_url'].endswith('/test')
    for local in ('https://evil.example:1234','http://127.0.0.1:4000','https://127.0.0.1:4000/x','https://user@127.0.0.1:4000'):
        remote._event({'phase':'online','local_url':local});assert found[-1] is None
    remote._event({'phase':'online','local_url':'https://127.0.0.1:4200'})
    assert found[-1]=='https://127.0.0.1:4200'
    remote.last_event-=21;assert remote.status()['phase']=='reconnecting'
    remote.config=vpn.validate_config({'enabled':True,'provider':'headscale','control_url':'https://vpn.example.org'})
    remote._event({'phase':'needs_login','auth_url':'https://login.tailscale.com/a/test'})
    assert 'auth_url' not in remote.status()
    remote._event({'phase':'needs_login','auth_url':'https://vpn.example.org/register/test'})
    assert 'auth_url' in remote.status()
    remote.close();assert found[-1] is None

def test_supervisor_persists_restarts_and_stops_own_helper(tmp_path,monkeypatch):
    script=tmp_path/'fake.py';script.write_text('''import sys,json
c=json.loads(sys.stdin.readline())
assert c['mode']=='employee' and c['target'].startswith('nexsearch-')
assert 'auth_key' not in c
print(json.dumps({'phase':'online','local_url':'https://127.0.0.1:4200'}),flush=True)
for command in sys.stdin:
 if command.strip()=='stop':break
''')
    original=subprocess.Popen;children=[]
    monkeypatch.setattr(vpn,'helper_path',lambda:script)
    monkeypatch.setenv('TS_AUTHKEY','must-not-leak');monkeypatch.setenv('TS_CLIENT_ID','must-not-leak')
    def spawn(args,**kw):
        assert not any(k.startswith('TS_') and k!='TS_NO_LOGS_NO_SUPPORT' for k in kw['env'])
        child=original([sys.executable,str(script)],**kw);children.append(child);return child
    monkeypatch.setattr(vpn.subprocess,'Popen',spawn)
    remote=vpn.RemoteAccess(tmp_path/'state',TRUST,device='device1');remote.configure({'enabled':True})
    try:
        deadline=time.monotonic()+8
        while remote.status()['phase']!='online' and time.monotonic()<deadline:time.sleep(.02)
        assert remote.status()['phase']=='online'
        assert vpn.RemoteAccess(tmp_path/'state',TRUST).config['enabled']
        remote.configure({'enabled':False})
        assert remote.status()['phase']=='disabled' and all(p.poll() is not None for p in children)
        assert not vpn.RemoteAccess(tmp_path/'state',TRUST).config['enabled']
    finally:remote.close()

def test_lan_is_preferred_and_vpn_requires_same_pinned_host(tmp_path):
    agent=EmployeeAgent(TRUST,tmp_path,profile={'host_url':'https://192.168.0.102:8743'})
    requested=[];lan_online=True;identity=TRUST['host_id']
    def get(url,**kw):
        requested.append((url,kw));assert kw['allow_redirects'] is False
        if url.startswith(agent.lan_url) and not lan_online:raise requests.ConnectionError()
        return SimpleNamespace(ok=True,json=lambda:{'host_id':identity})
    agent.session.get=get
    try:
        agent.remote_found('https://127.0.0.1:4200');assert agent.connection_mode=='lan'
        lan_online=False;agent.last_lan_probe=0
        identity='another-host';agent.remote_found('https://127.0.0.1:4200');assert agent.connection_mode=='lan'
        identity=TRUST['host_id'];agent.last_lan_probe=0
        agent.remote_found('https://127.0.0.1:4200');assert agent.connection_mode=='vpn'
        assert agent.url=='https://127.0.0.1:4200'
        assert secure_store.read(agent.path)['host_url']=='https://192.168.0.102:8743'
        adapter=agent.session.get_adapter(agent.url);assert adapter.fingerprint==TRUST['fingerprint']
        lan_online=True;agent.remote_found('https://127.0.0.1:4200');assert agent.connection_mode=='lan'
        lan_online=False;agent.last_lan_probe=0;agent.remote_found('https://127.0.0.1:4200')
        agent.remote_found(None);assert agent.url==agent.lan_url and agent.connection_mode=='lan'
    finally:agent.vpn.close();agent.session.close()

def test_bridge_vpn_mutations_require_origin_and_csrf(tmp_path):
    root=Path(__file__).resolve().parents[2]
    server,agent,close=start_client(TRUST,tmp_path,root/'network/web',port=0,open_browser=False,discovery=False)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url='http://127.0.0.1:'+str(server.server_port)
    try:
        assert requests.get(url+'/api/client/vpn/status').json()['phase']=='disabled'
        endpoint=url+'/api/client/vpn/config';body={'enabled':False}
        assert requests.post(endpoint,json=body).status_code==403
        assert requests.post(endpoint,json=body,headers={'Origin':'https://evil.example','X-Nexol-CSRF':agent.csrf}).status_code==403
        assert requests.post(endpoint,json=body,headers={'Origin':url,'X-Nexol-CSRF':agent.csrf}).status_code==200
        endpoint=url+'/api/client/transport'
        assert requests.post(endpoint,json={'mode':'lan'}).status_code==403
        assert requests.post(endpoint,json={'mode':'unknown'},headers={'Origin':url,'X-Nexol-CSRF':agent.csrf}).status_code==400
        assert requests.post(endpoint,json={'mode':'lan'},headers={'Origin':url,'X-Nexol-CSRF':agent.csrf}).json()['preference']=='lan'
    finally:server.shutdown();close();thread.join(5)

def test_forced_vpn_cannot_fall_back_to_healthy_lan_and_choice_survives_restart(tmp_path):
    agent=EmployeeAgent(TRUST,tmp_path,profile={'host_url':'https://192.168.0.102:8743'})
    agent.vpn.config['enabled']=True  # No real external enrollment in unit tests.
    agent.session.get=lambda *args,**kw:SimpleNamespace(ok=True,json=lambda:{'host_id':TRUST['host_id']})
    try:
        state=agent.select_transport('vpn');assert state['preference']=='vpn' and not state['host_found']
        with pytest.raises(ConnectionError):agent.request('/api/state')
        agent.found(agent.lan_url);assert agent.url is None
        agent.remote_found('https://127.0.0.1:4200');assert agent.url=='https://127.0.0.1:4200'
        agent.found(agent.lan_url);assert agent.connection_mode=='vpn'
        restarted=EmployeeAgent(TRUST,tmp_path)
        assert restarted.preference=='vpn' and restarted.url is None
        restarted.vpn.close();restarted.session.close()
        agent.remote_found(None);assert agent.url is None
        assert agent.select_transport('lan')['transport']=='lan'
        agent.remote_found('https://127.0.0.1:4200');assert agent.url==agent.lan_url
        assert secure_store.read(agent.path)['connection_preference']=='lan'
    finally:agent.vpn.close();agent.session.close()

def test_only_director_can_enroll_office_host(db,tmp_path):
    a,b,manager,director,outside=staff(db);api=http(db)
    remote=vpn.RemoteAccess(tmp_path,TRUST,mode='host');api.app.state.remote_access=remote
    remote._event({'phase':'needs_login','auth_url':'https://login.tailscale.com/a/test'})
    assert api.get('/api/remote/status').status_code==401
    for person in (a,manager):
        state=api.get('/api/remote/status',headers=header(person)).json()
        assert 'auth_url' not in state and 'config' not in state
        assert api.post('/api/remote/config',headers=header(person),json={'enabled':False}).status_code==403
        assert api.post('/api/remote/login',headers=header(person),json={}).status_code==403
    assert 'auth_url' in api.get('/api/remote/status',headers=header(director)).json()
    assert api.post('/api/remote/config',headers=header(director),json={'enabled':'bad'}).status_code==422
    assert api.post('/api/remote/config',headers=header(director),json={'enabled':False}).status_code==200
    remote.close()

def test_source_packages_only_ship_their_native_helpers(tmp_path,monkeypatch):
    import package_update,zipfile
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    monkeypatch.setattr(package_update,'ROOT',tmp_path)
    (tmp_path/'version.json').write_text('{"version":"1.6.0","schema":16}')
    (tmp_path/'network').mkdir();(tmp_path/'network/client-trust.json').write_text('{}')
    for platform in ('windows-amd64','linux-amd64','darwin-amd64','darwin-arm64'):
        folder=tmp_path/'network/bin'/platform;folder.mkdir(parents=True);(folder/'nexsearch-vpn').write_bytes(b'fake')
    for role,prefix in (('HostUbuntu','linux-'),('EmployeeMac','darwin-')):
        archive=tmp_path/(role+'.zip');package_update.package(role,archive,Ed25519PrivateKey.generate(),'test')
        with zipfile.ZipFile(archive) as z:
            native=[name for name in z.namelist() if '/network/bin/' in name]
            assert native and all('/network/bin/'+prefix in name for name in native)
