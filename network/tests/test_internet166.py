from test_crm import db,pg,users,item,http,header
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timezone,timedelta
import json,threading,time,requests,socket,os,subprocess,ssl,importlib.util
import pytest
from network.internet_access import validate_profile,load_profile,public_url,ProfileError,offline_candidates,healthy
from network.client import EmployeeAgent,PinnedAdapter,start_client
from network import secure_store
from network.tls import identity

ROOT=Path(__file__).resolve().parents[2]
TRUST={'host_id':'e5fa46a8-98fd-4138-acb8-3c6503f01e22','fingerprint':'a'*64}
PROFILE={'version':1,**TRUST,'url':'https://gateway.example.org'}

def test_profile_contains_only_a_matching_public_endpoint(tmp_path):
    assert validate_profile(PROFILE,TRUST)['url']=='https://gateway.example.org'
    for bad in ['http://gateway.example.org','https://user:secret@gateway.example.org','https://127.0.0.1','https://192.168.1.1','https://gateway.example.org/path','https://gateway.example.org?q=x','https://gateway.example.org:0','file:///x']:
        with pytest.raises(ProfileError):validate_profile({**PROFILE,'url':bad},TRUST)
    for bad in [{**PROFILE,'token':'must-not-ship'},{**PROFILE,'fingerprint':'b'*64},{**PROFILE,'host_id':'different'}]:
        with pytest.raises(ProfileError):validate_profile(bad,TRUST)
    (tmp_path/'internet-access.json').write_text(json.dumps(PROFILE))
    assert load_profile(tmp_path,TRUST)==(PROFILE,'')
    (tmp_path/'internet-access.json').write_text('{invalid')
    found,error=load_profile(tmp_path,TRUST);assert found is None and error and 'invalid' not in error

def test_internet_and_lan_choices_are_explicit_and_persist(tmp_path):
    agent=EmployeeAgent(TRUST,tmp_path,profile={'host_url':'https://192.168.0.2:8743'},internet_profile=PROFILE)
    agent.session.get=lambda *args,**kw:SimpleNamespace(status_code=200,json=lambda:{'host_id':TRUST['host_id']})
    try:
        agent.internet_health(PROFILE['url'],True);assert agent.connection_mode=='lan'
        agent.select_transport('internet');assert agent.url==PROFILE['url'] and agent.connection_mode=='internet'
        agent.found(agent.lan_url);assert agent.connection_mode=='internet'
        agent.internet_health(PROFILE['url'],False);assert agent.url is None
        agent.found(agent.lan_url);assert agent.url is None
        agent.internet_health(PROFILE['url'],True);assert agent.connection_mode=='internet'
        agent.select_transport('lan');assert agent.connection_mode=='lan'
        agent.internet_health(PROFILE['url'],True);assert agent.connection_mode=='lan'
        agent.select_transport('auto');agent.lan_health(agent.lan_url,False);assert agent.connection_mode=='internet'
        agent.lan_health(agent.lan_url,True);assert agent.connection_mode=='lan'
        agent.select_transport('internet');restarted=EmployeeAgent(TRUST,tmp_path,internet_profile=PROFILE)
        try:assert restarted.preference=='internet' and restarted.url is None
        finally:restarted.internet.close();restarted.vpn.close();restarted.session.close()
        assert offline_candidates(secure_store.read(agent.path),PROFILE)==[PROFILE['url']]
        agent.select_transport('lan');assert offline_candidates(secure_store.read(agent.path),PROFILE)==[agent.lan_url]
    finally:agent.internet.close();agent.vpn.close();agent.session.close()

def test_missing_profile_cannot_turn_on_vpn_or_silently_use_lan(tmp_path):
    agent=EmployeeAgent(TRUST,tmp_path,profile={'host_url':'https://192.168.1.2:8743'})
    try:
        state=agent.select_transport('internet');assert not state['host_found'] and not state['internet_configured']
        assert not agent.vpn.config['enabled']
        with pytest.raises(ConnectionError):agent.request('/api/state')
        assert offline_candidates({'connection_preference':'internet','host_url':agent.lan_url},None)==[]
    finally:agent.internet.close();agent.vpn.close();agent.session.close()

def test_bridge_recheck_is_protected_by_origin_and_csrf(tmp_path):
    server,agent,close=start_client(TRUST,tmp_path,ROOT/'network/web',port=0,open_browser=False,discovery=False,internet_profile=PROFILE)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url='http://127.0.0.1:'+str(server.server_port)
    try:
        path=url+'/api/client/internet/check';assert requests.post(path,json={}).status_code==403
        assert requests.post(path,json={},headers={'Origin':url,'X-Nexol-CSRF':agent.csrf}).status_code==200
        assert requests.get(url+'/api/client/transport').json()['internet_url']==PROFILE['url']
    finally:server.shutdown();close();thread.join(5)

def generator():
    spec=importlib.util.spec_from_file_location('tunnel_generator',ROOT/'ubuntu/tunnel/generate.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def test_generated_roles_have_no_shared_client_secrets_and_restrict_ports(tmp_path):
    import tomllib
    module=generator();folder=tmp_path/'bundle';module.generate('gateway.example.org',TRUST,folder)
    profile=json.loads((folder/'employees/internet-access.json').read_text());assert profile==PROFILE
    assert [p.name for p in (folder/'employees').iterdir()]==['internet-access.json']
    gateway=tomllib.loads((folder/'gateway/frps.toml').read_text());office=tomllib.loads((folder/'office/frpc.toml').read_text())
    assert gateway['allowPorts']==[{'single':443}] and gateway['maxPortsPerClient']==1 and gateway['transport']['tls']['force']
    assert office['proxies'][0]['localPort']==8743 and office['proxies'][0]['localIP']=='127.0.0.1' and office['loginFailExit'] is False
    assert (folder/'gateway/control.token').read_bytes()==(folder/'office/control.token').read_bytes()
    assert not (folder/'office/ca-private.pem').exists() and not (folder/'gateway/ca-private.pem').exists()
    with pytest.raises(ValueError):module.generate('gateway.example.org',TRUST,folder)
    with pytest.raises(ValueError):module.generate('gateway.example.org',TRUST,ROOT/'preview/must-not-contain-secrets')

def test_signed_source_packages_bind_public_profile_to_the_correct_host(tmp_path,monkeypatch):
    import package_update,zipfile
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    monkeypatch.setattr(package_update,'ROOT',tmp_path)
    (tmp_path/'version.json').write_text('{"version":"1.6.6","schema":19}')
    (tmp_path/'network').mkdir();(tmp_path/'network/client-trust.json').write_text(json.dumps(TRUST))
    deployment=tmp_path/'deployment';deployment.mkdir();(deployment/'internet-access.json').write_text(json.dumps(PROFILE));(deployment/'control.token').write_text('private-tunnel-token-must-not-ship')
    archive=tmp_path/'canonical.zip';signing=Ed25519PrivateKey.generate();package_update.package('EmployeeMac',archive,signing,'test166')
    with zipfile.ZipFile(archive) as z:
        assert json.loads(z.read('EmployeeMac/internet-access.json'))==PROFILE
        assert json.loads(z.read('EmployeeMac/network/internet-access.json'))==PROFILE
        assert not any('control.token' in n or '/deployment/' in n for n in z.namelist())
    other={**TRUST,'host_id':'926af581-69a7-4dcc-9b02-fdc6b2e2fadf'};override=tmp_path/'other.json';override.write_text(json.dumps(other));archive=tmp_path/'other.zip'
    package_update.package('EmployeeMac',archive,signing,'test166',override)
    with zipfile.ZipFile(archive) as z:assert not any(n.endswith('/internet-access.json') for n in z.namelist())

@pytest.mark.skipif(os.name!='nt' or not (ROOT/'preview/frp0711/frpc.exe').exists(),reason='Real pinned FRP binaries needed for the integration test')
def test_real_frp_tunnel_tls_privacy_excel_reconnect_and_no_replayed_write(db,tmp_path,monkeypatch):
    import uvicorn
    from network.api import create_app
    from uuid import uuid4
    def free():
        with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
    control,public,local=free(),free(),free();trust,cert,key=identity(tmp_path/'identity');bundle=tmp_path/'bundle'
    generator().generate('relay.nexsearch.test',trust,bundle,control,public,local)
    config_paths={}
    for role,name in [('gateway','frps'),('office','frpc')]:
        p=bundle/role/(name+'.toml');text=p.read_text().replace('/etc/nexsearch-tunnel/',(bundle/role).as_posix()+'/').replace('"0.0.0.0"','"127.0.0.1"')
        if role=='office':text=text.replace('serverAddr = "relay.nexsearch.test"','serverAddr = "127.0.0.1"')
        p.write_text(text,encoding='utf-8');config_paths[name]=p
        result=subprocess.run([str(ROOT/f'preview/frp0711/{name}.exe'),'verify','-c',str(p)],capture_output=True,timeout=10)
        assert result.returncode==0,result.stderr.decode(errors='replace')
    original_dns=socket.getaddrinfo
    monkeypatch.setattr(socket,'getaddrinfo',lambda host,*args,**kw:original_dns('127.0.0.1' if host=='relay.nexsearch.test' else host,*args,**kw))
    a,b=users(db);db.ingest(item(166),a['employee']['id']);db.ingest(item(167),b['employee']['id'])
    app=create_app(db,pack=ROOT/'pack',start_worker=False);monkeypatch.setenv('NEXOL_HOST_ID',trust['host_id'])
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=local,ssl_certfile=str(cert),ssl_keyfile=str(key),access_log=False,log_level='error',timeout_graceful_shutdown=1))
    thread=threading.Thread(target=server.run,daemon=True);thread.start();children=[];logs=[];agent=None
    def start(name,path=None):
        log=(tmp_path/(name+'-'+str(len(logs))+'.log')).open('wb');logs.append(log)
        child=subprocess.Popen([str(ROOT/f'preview/frp0711/{name}.exe'),'-c',str(path or config_paths[name])],stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW);children.append(child);return child
    def until(test,timeout=12):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if test():return
            time.sleep(.1)
        raise AssertionError('Tunnel did not reach the expected state before deadline')
    try:
        until(lambda:server.started);start('frps');client=start('frpc')
        profile=json.loads((bundle/'employees/internet-access.json').read_text())
        agent=EmployeeAgent(trust,tmp_path/'employee',internet_profile=profile);agent.select_transport('internet');agent.internet.interval=.2;agent.internet.start()
        until(lambda:agent.transport()['internet_online'])
        code,_,body=agent.request('/api/auth/login','POST',json.dumps({'login':a['employee']['login'],'password':'password-one'}).encode());assert code==200,body
        code,_,body=agent.request('/api/leads');companies=json.loads(body)['companies'];assert code==200 and len(companies)==1 and companies[0]['inn']==item(166)['inn']
        code,headers,data=agent.request('/api/export/leads');assert code==200 and data.startswith(b'PK') and 'spreadsheet' in headers['Content-Type']
        # The scheduled updater must also work with the app closed: no LAN URL,
        # a provisioned public profile, pinned TLS and a large signed download.
        import zipfile,hashlib
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        from network.daily_updates import check_update
        signing=Ed25519PrivateKey.generate();program=tmp_path/'program';program.mkdir()
        (program/'updates-public.pem').write_bytes(signing.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
        (program/'internet-access.json').write_text(json.dumps(profile));(program/'package-info.json').write_text(json.dumps({'version':'1.6.5','source_commit':'older-test-build'}))
        payload=os.urandom(3*1024*1024);meta={'version':'1.6.6','schema':19,'role':'EmployeeWindows','source_commit':'real-relay-test'}
        files={'EmployeeWindows/payload.bin':payload,'EmployeeWindows/package-info.json':json.dumps(meta).encode()};manifest={**meta,'files':{n:hashlib.sha256(data).hexdigest() for n,data in files.items()}}
        packets=tmp_path/'packages';packets.mkdir();archive=packets/'pending.zip'
        with zipfile.ZipFile(archive,'w') as z:
            raw=json.dumps(manifest,sort_keys=True).encode();z.writestr('release.json',raw);z.writestr('release.sig',signing.sign(raw))
            for name,data in files.items():z.writestr(name,data)
        digest=hashlib.sha256(archive.read_bytes()).hexdigest();archive.rename(packets/(digest+'.zip'))
        state_file=tmp_path/'updates.json';state_file.write_text(json.dumps({'checked_at':datetime.now(timezone.utc).isoformat(),'packages':{'EmployeeWindows':{**meta,'sha256':digest,'size_bytes':(packets/(digest+'.zip')).stat().st_size}}}))
        monkeypatch.setenv('NEXSEARCH_UPDATE_STATE',str(state_file));monkeypatch.setenv('NEXSEARCH_UPDATE_PACKAGES',str(packets))
        update=check_update(program,agent.home,trust,force=True)
        assert update['phase']=='ready' and update['version']=='1.6.6',update
        pending=json.loads(Path(update['pending_state']).read_text());assert (Path(pending['stage'])/'payload.bin').read_bytes()==payload
        assert not healthy(agent.session,profile['url'],str(uuid4()))
        wrong=requests.Session();wrong.trust_env=False;wrong.mount('https://',PinnedAdapter('b'*64))
        try:assert not healthy(wrong,profile['url'],trust['host_id'])
        finally:wrong.close()
        original=agent.session.request;calls=[]
        def committed_then_disconnected(method,url,**kw):
            calls.append((method,url));response=original(method,url,**kw)
            if method=='POST' and url.endswith('/api/tasks'):
                assert response.status_code==200;raise requests.ReadTimeout('Simulated lost acknowledgement after commit')
            return response
        agent.session.request=committed_then_disconnected
        with pytest.raises(requests.ReadTimeout):agent.request('/api/tasks','POST',json.dumps({'title':'One write through the relay','due_at':(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()}).encode())
        assert sum(method=='POST' for method,_ in calls)==1
        with db.transaction(admin=True) as c:assert c.execute("SELECT count(*) AS n FROM followup_tasks WHERE title='One write through the relay'").fetchone()['n']==1
        agent.session.request=original
        client.terminate();client.wait(timeout=10);agent.internet.nudge();until(lambda:not agent.transport()['internet_online'])
        assert agent.url is None
        # The server requires both a trusted client certificate and the control token.
        text=config_paths['frpc'].read_text();bad=bundle/'office/bad.toml';bad_token=bundle/'office/bad.token';bad_token.write_text('unauthorized')
        bad.write_text(text.replace('loginFailExit = false','loginFailExit = true').replace('control.token','bad.token'))
        denied=start('frpc',bad);assert denied.wait(timeout=10)!=0
        bad.write_text('\n'.join(line for line in text.replace('loginFailExit = false','loginFailExit = true').splitlines() if not line.startswith(('transport.tls.certFile','transport.tls.keyFile'))))
        denied=start('frpc',bad);assert denied.wait(timeout=10)!=0
        start('frpc');agent.internet.nudge();until(lambda:agent.transport()['internet_online'])
        assert agent.request('/api/leads')[0]==200
    finally:
        if agent:agent.internet.close();agent.vpn.close();agent.session.close()
        for child in reversed(children):
            if child.poll() is None:child.terminate();child.wait(timeout=10)
        for log in logs:log.close()
        server.should_exit=True;thread.join(timeout=10)
