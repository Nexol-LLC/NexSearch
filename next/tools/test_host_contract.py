"""Real TLS + PostgreSQL contract tests. A new cluster per run; no work data."""
from pathlib import Path
from uuid import uuid4
import hashlib, json, os, subprocess, sys, time
from urllib.parse import quote
import pytest, requests

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from network.database import Database
from network.auth import HASHER, digest
from network.postgres_runtime import start_postgres,stop_postgres
from network.tls import identity
from network.client import PinnedAdapter
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

@pytest.fixture(scope='module')
def service():
    home=ROOT/'next/local-data'/('host-contract-'+uuid4().hex)
    home.mkdir(parents=True)
    runtime=Path(os.environ.get('NEXSEARCH_TEST_POSTGRES',ROOT/'preview/HostWindows/postgres'))
    dsn=start_postgres(home,runtime,54359)
    db=Database(dsn)
    with db.transaction(admin=True) as c:
        for name in ('schema.sql','schema14.sql','schema15.sql','schema16.sql','schema17.sql','schema18.sql','schema19.sql'):
            c.execute((ROOT/'network'/name).read_text(encoding='utf8'))
    trust,cert,key=identity(home/'identity')
    info=conninfo_to_dict(dsn)
    url='postgres://'+quote(info['user'],safe='')+':'+quote(info['password'],safe='')+'@127.0.0.1:'+info['port']+'/'+info['dbname']
    config={'database_url':url,'host_id':trust['host_id'],'bind':'127.0.0.1:18744','tls_cert':str(cert),'tls_key':str(key),'secret_file':str(home/'host-secrets.enc'),'web_dir':str(ROOT/'network/web'),'pack_dir':str(ROOT/'pack'),'update_dir':str(home/'updates'),'source_access':False}
    file=home/'config.json';file.write_text(json.dumps(config),encoding='utf8')
    out=(home/'server.log').open('wb');env={**os.environ,'NEXSEARCH_HOST_CONFIG':str(file)}
    process=subprocess.Popen([str(ROOT/'next/target/debug/nexsearch-host.exe')],env=env,stdout=out,stderr=out,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    session=requests.Session();session.trust_env=False;session.mount('https://',PinnedAdapter(trust['fingerprint']))
    base='https://127.0.0.1:18744'
    try:
        for _ in range(200):
            if process.poll() is not None:raise AssertionError((home/'server.log').read_text(errors='replace'))
            try:
                if session.get(base+'/health',timeout=1).status_code==200:break
            except requests.RequestException:pass
            time.sleep(.1)
        else:raise AssertionError('Native TLS host did not start')
        yield session,base,db,home
    finally:
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait()
        out.close();session.close();db.close();stop_postgres(home,runtime)

@pytest.fixture(scope='module')
def people(service):
    session,base,db,_=service;people={}
    # An old-format password and bearer session must work without re-registration.
    with db.transaction(admin=True) as c:
        for name,role,department in [('alice','logist','one'),('bob','logist','one'),('outside','logist','two'),('manager','manager','one'),('director','director','main')]:
            ident=uuid4();token='old-session-'+uuid4().hex
            c.execute('INSERT INTO employees(id,login,first_name,last_name,password_hash,role,requested_role,department,approved) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,true)',(ident,name,name,'Сотрудник',HASHER.hash('legacy-password-123'),role,role,department))
            c.execute("INSERT INTO sessions(token_hash,employee_id,device_id,expires_at) VALUES(%s,%s,%s,now()+interval '30 days')",(digest(token),ident,'legacy-'+name))
            people[name]={'id':str(ident),'headers':{'Authorization':'Bearer '+token}}
    return people

def request(service,people,name,method,path,**kwargs):
    session,base,_,_=service
    return session.request(method,base+path,headers=people[name]['headers'],timeout=10,**kwargs)

def test_existing_password_and_session_are_accepted_without_hash_leaks(service,people):
    session,base,_,_=service
    assert session.get(base+'/api/me',timeout=5).status_code==401
    me=request(service,people,'alice','GET','/api/me');assert me.status_code==200,me.text
    assert me.json()['employee']['id']==people['alice']['id']
    assert 'password_hash' not in me.text and 'old-session-' not in me.text
    login=session.post(base+'/api/auth/login',json={'login':'alice','password':'legacy-password-123','device_id':'new-native-device','computer_name':'Native contract'},timeout=10)
    assert login.status_code==200,login.text
    assert len(login.json()['token'])>=40
    assert 'password_hash' not in login.text
    wrong=session.post(base+'/api/auth/login',json={'login':'alice','password':'wrong','device_id':'invalid-device'},timeout=10)
    assert wrong.status_code==401

@pytest.fixture(scope='module')
def company(service,people):
    _,_,db,_=service;ident=uuid4();other=uuid4()
    with db.transaction(admin=True) as c:
        for uid,name,owner,inn in [(ident,'Компания контактов',people['alice']['id'],'7700000016'),(other,'Другой отдел',people['outside']['id'],'7700000023')]:
            c.execute("INSERT INTO companies(id,name,inn,payload,active_verified,verified_at) VALUES(%s,%s,%s,%s,true,now())",(uid,name,inn,Jsonb({'name':name,'inn':inn,'phones':['+7 495 100-10-10'],'emails':['source@example.org'],'region':'Москва'})))
            c.execute("INSERT INTO employee_leads(company_id,employee_id,status,called) VALUES(%s,%s,'taken',true)",(uid,owner))
            c.execute('INSERT INTO employee_clients(company_id,employee_id,stage) VALUES(%s,%s,%s)',(uid,owner,'Переговоры'))
    return str(ident),str(other)

def test_company_privacy_and_department_kanban(service,people,company):
    ident,outside=company
    assert request(service,people,'bob','GET','/api/companies/'+ident).status_code==404
    assert request(service,people,'manager','GET','/api/companies/'+ident).status_code==200
    assert request(service,people,'manager','GET','/api/companies/'+outside).status_code==404
    assert request(service,people,'alice','GET','/api/clients/overview').status_code==403
    overview=request(service,people,'manager','GET','/api/clients/overview');assert overview.status_code==200,overview.text
    assert {r['id'] for r in overview.json()['companies']}=={ident}
    assert request(service,people,'director','GET','/api/clients/overview').status_code==200

def test_added_contacts_are_revision_checked_private_and_keep_sources(service,people,company):
    ident,_=company;path='/api/leads/'+ident+'/contacts'
    body={'phones':[' +7 900 200-20-20 ','+7 900 200-20-20'],'emails':['working@example.org'],'revision':0}
    assert request(service,people,'bob','PATCH',path,json=body).status_code in (403,404)
    result=request(service,people,'alice','PATCH',path,json=body);assert result.status_code==200,result.text
    assert result.json()['contacts_revision']==1 and len(result.json()['phones'])==1
    assert request(service,people,'alice','PATCH',path,json=body).status_code==409
    card=request(service,people,'alice','GET','/api/companies/'+ident).json()
    assert card['phones']==['+7 900 200-20-20'] and card['source_contacts']['phones']==['+7 495 100-10-10']
    added=request(service,people,'alice','POST','/api/contacts',json={'company_id':ident,'name':'Анна','phones':'+7 900 333-44-55','email':'person@example.org'})
    assert added.status_code==200,added.text
    assert request(service,people,'alice','GET','/api/contacts?company_id='+ident).json()['contacts'][0]['email']=='person@example.org'
    assert request(service,people,'bob','GET','/api/contacts?company_id='+ident).status_code==404
    invalid=request(service,people,'alice','PATCH',path,json={**body,'revision':1,'emails':['bad\nheader@example.org']});assert invalid.status_code==422

def test_transfer_is_atomic_and_preserves_contacts_stage_and_history(service,people,company):
    ident,_=company;path='/api/companies/'+ident+'/transfer'
    card=request(service,people,'manager','GET','/api/companies/'+ident).json()
    body={'employee_id':people['bob']['id'],'revision':card['revision'],'note':'Передача с сохранением истории'}
    assert request(service,people,'alice','POST',path,json=body).status_code==403
    assert request(service,people,'manager','POST',path,json={**body,'employee_id':people['outside']['id']}).status_code==404
    assert request(service,people,'manager','POST',path,json={**body,'revision':999}).status_code==409
    result=request(service,people,'manager','POST',path,json=body);assert result.status_code==200,result.text
    assert request(service,people,'alice','GET','/api/companies/'+ident).status_code==404
    card=request(service,people,'bob','GET','/api/companies/'+ident).json();assert card['stage']=='Переговоры' and card['emails']==['working@example.org']
    assert len(request(service,people,'bob','GET','/api/contacts?company_id='+ident).json()['contacts'])==1
    history=request(service,people,'bob','GET','/api/companies/'+ident+'/interactions');assert history.status_code==200,history.text
    assert any(r['kind']=='company.transfer' for r in history.json()['interactions'])


@pytest.fixture(scope='module')
def mailbox_cache(service,people):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _,_,db,home=service
    account,message=uuid4(),uuid4();owner=people['alice']['id']
    # Exact 1.6.7 encrypted password layout; a new host must keep this readable.
    key=hashlib.sha256((home/'identity'/'host-key.pem').read_bytes()+b'NexSearch personal mail 1.6').digest()
    nonce=os.urandom(12);secret=nonce+AESGCM(key).encrypt(nonce,b'fixture-app-password',f'{owner}:{account}'.encode())
    with db.transaction(admin=True) as c:
        c.execute('INSERT INTO mail_accounts(id,employee_id,email,login,label,imap_host,smtp_host,secret,folders) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',(account,owner,'me@example.org','me@example.org','Личная почта','imap.example.org','smtp.example.org',secret,Jsonb([{'id':'INBOX','name':'Входящие','selectable':True}])) )
        c.execute("INSERT INTO mail_messages(id,employee_id,account_id,folder,uidvalidity,uid,subject,sender,recipients,addresses,seen,body_text,body_html,content_version) VALUES(%s,%s,%s,'INBOX','42',1,'Сохранённое письмо','a@example.org','[]',ARRAY['a@example.org'],true,'Старый текст','<p>Сохранённый HTML</p>',2)",(message,owner,account))
    return str(account),str(message)


def test_native_mail_opens_old_cache_and_remains_private_even_from_director(service,people,mailbox_cache):
    account,message=mailbox_cache
    read=request(service,people,'alice','GET','/api/mail/messages/'+message)
    assert read.status_code==200,read.text
    assert read.json()['body_text']=='Старый текст'
    assert '<p>Сохранённый HTML</p>'==read.json()['body_html']
    assert 'uidvalidity' not in read.json() and 'employee_id' not in read.json()
    public=request(service,people,'alice','GET','/api/mail/accounts')
    assert public.status_code==200 and len(public.json()['accounts'])==1
    assert 'secret' not in public.text and 'fixture-app-password' not in public.text
    for person in ('bob','manager','director'):
        assert request(service,people,person,'GET','/api/mail/messages/'+message).status_code==404
        assert request(service,people,person,'GET','/api/mail/accounts').json()['accounts']==[]
        assert request(service,people,person,'POST',f'/api/mail/accounts/{account}/sync',json={}).status_code==404
        assert request(service,people,person,'PATCH',f'/api/mail/accounts/{account}/settings',json={'read_mode':'text'}).status_code==404


def test_native_mail_settings_and_failure_release_account_lock(service,people,mailbox_cache):
    account,_=mailbox_cache;path=f'/api/mail/accounts/{account}'
    result=request(service,people,'alice','PATCH',path+'/settings',json={'read_mode':'html','signature':'С уважением','notifications':False,'sync_folders':['INBOX']})
    assert result.status_code==200,result.text
    assert result.json()['saved'] is True
    invalid=request(service,people,'alice','PATCH',path+'/settings',json={'read_mode':'active-script'})
    assert invalid.status_code==422,invalid.text
    for _ in range(2):
        failed=request(service,people,'alice','POST',path+'/sync',json={})
        assert failed.status_code==503,failed.text
        assert failed.json().get('syncing') is not True
    saved=request(service,people,'alice','GET','/api/mail/accounts').json()['accounts'][0]
    assert saved['settings']['read_mode']=='html' and saved['last_error']


def test_pending_registration_can_check_approval_but_cannot_read_business_data(service):
    session,base,_,_=service
    register=session.post(base+'/api/auth/register',json={'first_name':'Новый','last_name':'Сотрудник','password':'  significant spaces  ','device_id':'pending-device'},timeout=10)
    assert register.status_code==200,register.text
    headers={'Authorization':'Bearer '+register.json()['token']}
    status=session.get(base+'/api/auth/status',headers=headers,timeout=5)
    assert status.status_code==200 and status.json()['approved'] is False
    assert session.get(base+'/api/leads',headers=headers,timeout=5).status_code==403
    login=register.json()['employee']['login']
    entered=session.post(base+'/api/auth/login',json={'login':login,'password':'  significant spaces  ','device_id':'pending-device2'},timeout=10)
    assert entered.status_code==200,entered.text
    trimmed=session.post(base+'/api/auth/login',json={'login':login,'password':'significant spaces','device_id':'pending-device3'},timeout=10)
    assert trimmed.status_code==401


def test_native_host_serves_interface_without_exposing_server_sources(service):
    session,base,_,_=service
    page=session.get(base+'/',timeout=5)
    assert page.status_code==200 and '<!doctype html>' in page.text.lower()
    assert 'NexSearch 2.0.0-alpha.1' in page.text
    assert page.headers['X-Content-Type-Options']=='nosniff'
    script=session.get(base+'/assets/app.js',timeout=5)
    assert script.status_code==200 and 'javascript' in script.headers['Content-Type']
    models=session.get(base+'/pack/src/models.js',timeout=5)
    assert models.status_code==200 and 'cargoRotationAllowed' in models.text
    for path in ('/assets/%2e%2e/schema.sql','/pack/%2e%2e/network/schema.sql','/schema.sql','/config.json'):
        response=session.get(base+path,timeout=5)
        assert response.status_code in (400,404),path
        assert 'CREATE TABLE' not in response.text


def test_native_host_distributes_prepared_employee_package_only_after_login(service,people):
    session,base,_,home=service;updates=home/'updates';cache=updates/'packages';cache.mkdir(parents=True,exist_ok=True)
    # Distribution contract only. Signature validation belongs to the separate
    # privileged preparer and employee installer, not this fixture.
    payload=b'fixture package stream: not a release';digest=hashlib.sha256(payload).hexdigest()
    package={'version':'2.0.0','source_commit':'a'*40,'sha256':digest,'size_bytes':len(payload)}
    (cache/(digest+'.zip')).write_bytes(payload)
    (updates/'status.json').write_text(json.dumps({'checked_at':'2026-11-11T07:00:00+00:00','packages':{'EmployeeWindows':package}}),encoding='utf8')
    assert session.get(base+'/api/update/info',timeout=5).status_code==401
    info=request(service,people,'alice','GET','/api/update/info')
    assert info.status_code==200 and info.json()['available'] and info.json()['sha256']==digest
    path='/api/update/package?platform=EmployeeWindows&sha256='+digest
    assert session.get(base+path,timeout=5).status_code==401
    download=request(service,people,'alice','GET',path)
    assert download.status_code==200 and download.content==payload
    assert download.headers['Content-Type']=='application/zip'
    assert request(service,people,'alice','GET','/api/update/package?sha256=../../config.json').status_code==409
    assert request(service,people,'director','GET','/api/update/info?platform=HostUbuntu').status_code==400
    (cache/(digest+'.zip')).write_bytes(b'partial')
    assert request(service,people,'alice','GET',path).status_code==503


def test_named_loading_plans_keep_all_preferences_and_are_personal(service,people):
    project={'version':2,'trucks':[{'vehicle':{'L':6000,'W':2400,'H':2500,'maxPayload':20000}}],'cargo':[],'boxes':[],'cur':0,'seq':1,'settings':{'cargo_units':{'length':'cm','weight':'kg'},'packing_mode':'balance','view_mode':'top','camera':{'theta':1,'phi':1,'dist':25,'target':[3,2,1]}}}
    saved=request(service,people,'alice','POST','/api/plans',json={'name':'Моя постоянная погрузка','project':project})
    assert saved.status_code==200,saved.text
    plan=saved.json();path='/api/plans/'+plan['id']
    assert request(service,people,'alice','GET',path).json()['project']==project
    assert any(p['id']==plan['id'] for p in request(service,people,'alice','GET','/api/plans').json()['plans'])
    for employee in ('bob','manager','director'):
        assert request(service,people,employee,'GET',path).status_code==404
        assert not any(p['id']==plan['id'] for p in request(service,people,employee,'GET','/api/plans').json()['plans'])
        assert request(service,people,employee,'PUT',path,json={'name':'Чужое изменение','project':project,'revision':plan['revision']}).status_code in (403,409)
    changed=request(service,people,'alice','PUT',path,json={'name':'Обновлённая схема','project':project,'revision':plan['revision']})
    assert changed.status_code==200,changed.text
    assert request(service,people,'alice','PUT',path,json={'name':'Устаревшее изменение','project':project,'revision':plan['revision']}).status_code==409
