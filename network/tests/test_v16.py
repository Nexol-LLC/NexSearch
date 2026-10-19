from test_crm import db,pg,users,item,http,header
from datetime import datetime,timezone,timedelta
from email.message import EmailMessage
from uuid import uuid4
from pathlib import Path
import imaplib
import json
import pytest
from network.auth import Authentication
from network.company_time import company_time
from network.mailbox import content,folders_from_list,public_socket,MailError,seal,unseal
from network.checko import VerificationUnavailable

def staff(db):
    a,b=users(db);auth=Authentication(db)
    manager=auth.register('Роман','Руководитель','password-manager','manager-pc','','127.0.0.1')
    director=auth.register('Денис','Директор','password-director','director-pc','','127.0.0.1')
    outside=auth.register('Олег','Другой','password-outside','outside-pc','','127.0.0.1')
    with db.transaction(admin=True) as c:
        c.execute('UPDATE employees SET approved=true')
        c.execute("UPDATE employees SET role='manager' WHERE id=%s",(manager['employee']['id'],))
        c.execute("UPDATE employees SET role='director' WHERE id=%s",(director['employee']['id'],))
        c.execute("UPDATE employees SET department='outside' WHERE id=%s",(outside['employee']['id'],))
    return a,b,manager,director,outside

def task_body(**kw):
    return {'title':'Позвонить клиенту','description':'Уточнить условия','due_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),**kw}

def test_timezone_is_identical_for_leads_and_clients(db):
    a,b=users(db);api=http(db)
    ident,_,_=db.ingest({**item(1),'region':'Самарская область'},a['employee']['id'])
    lead=api.get('/api/leads',headers=header(a)).json()['companies'][0]
    assert lead['company_time']['utc_offset']==4 and 'МСК+1' in lead['company_time']['label']
    db.update_lead(a['employee']['id'],ident,'taken',True,'Готовы работать',0)
    client=api.get('/api/clients',headers=header(a)).json()['companies'][0]
    assert client['company_time']['utc_offset']==lead['company_time']['utc_offset']
    assert company_time({'region':'Неизвестный регион'})['utc_offset'] is None
    assert company_time({'region':'Республика Саха (Якутия)','legal_address':'г. Якутск, улица Ленина'})['utc_offset']==9
    assert company_time({'region':'Республика Саха (Якутия)'})['utc_offset'] is None

def test_inn_lookup_verifies_and_reserves_without_duplicates(db,monkeypatch):
    a,b=users(db);api=http(db);calls=[]
    def verify(inn):
        calls.append(inn);company=item(20,inn);company['verification']['source_url']='https://checko.ru/company/test';return company
    monkeypatch.setattr(api.app.state.checker,'verify',verify)
    inn=item(20)['inn']
    assert api.post('/api/search/inn',json={'inn':inn}).status_code==401
    response=api.post('/api/search/inn',headers=header(a),json={'inn':inn})
    assert response.status_code==200,response.text
    assert response.json()['added'] and response.json()['company']['inn']==inn
    assert api.post('/api/search/inn',headers=header(a),json={'inn':inn}).json()['added'] is False
    count=len(calls)
    denied=api.post('/api/search/inn',headers=header(b),json={'inn':inn})
    assert denied.status_code==409 and len(calls)==count
    assert len(db.leads(a['employee']['id']))==1 and not db.leads(b['employee']['id'])
    assert api.post('/api/search/inn',headers=header(a),json={'inn':'7700000000'}).status_code==422

def test_inn_lookup_rejects_inactive_and_reports_unavailable(db,monkeypatch):
    a,b=users(db);api=http(db);company=item(20);company['verification']['active']=False
    monkeypatch.setattr(api.app.state.checker,'verify',lambda _:company)
    assert api.post('/api/search/inn',headers=header(a),json={'inn':company['inn']}).status_code==422
    def unavailable(_):raise VerificationUnavailable('Чекко временно недоступен')
    monkeypatch.setattr(api.app.state.checker,'verify',unavailable)
    assert api.post('/api/search/inn',headers=header(a),json={'inn':company['inn']}).status_code==503
    assert not db.leads(a['employee']['id'])

def test_task_assignment_permissions_and_company_owner(db):
    a,b,manager,director,outside=staff(db);api=http(db)
    aid,bid,mid,did,oid=[str(p['employee']['id']) for p in (a,b,manager,director,outside)]
    assigned=api.get('/api/tasks/assignees',headers=header(manager)).json()['employees']
    assert {p['id'] for p in assigned}=={aid,bid,mid}
    assert len(api.get('/api/tasks/assignees',headers=header(director)).json()['employees'])==5
    for actor,target in [(a,bid),(manager,oid),(manager,did)]:
        assert api.post('/api/tasks',headers=header(actor),json=task_body(employee_id=target)).status_code==403
    own_company=str(db.ingest(item(11),a['employee']['id'])[0])
    assert api.post('/api/tasks',headers=header(manager),json=task_body(employee_id=bid,company_id=own_company)).status_code==404
    response=api.post('/api/tasks',headers=header(manager),json=task_body(employee_id=aid,company_id=own_company))
    assert response.status_code==200,response.text
    task=response.json();assert task['employee_id']==aid and task['assigned_by']==mid
    assert api.get('/api/tasks/'+task['id'],headers=header(b)).status_code==404
    result=api.get('/api/tasks',headers=header(a)).json()['tasks'];assert len(result)==1
    assert api.post('/api/tasks',headers=header(director),json=task_body(employee_id=oid)).status_code==200
    assert api.get('/api/tasks/companies?employee_id='+bid,headers=header(a)).status_code==403

def test_assigned_task_notifications_and_completion_reply(db):
    a,b,manager,director,outside=staff(db);api=http(db)
    response=api.post('/api/tasks',headers=header(manager),json=task_body(employee_id=str(a['employee']['id'])))
    assert response.status_code==200,response.text
    task=response.json();alerts=api.get('/api/notifications',headers=header(a)).json()
    assert alerts['unread']==1 and alerts['notifications'][0]['kind']=='task.assigned'
    ident=alerts['notifications'][0]['id']
    assert api.post('/api/notifications/'+ident+'/read',headers=header(b),json={}).status_code==404
    update={**task_body(status='done',revision=task['revision']), 'employee_id':task['employee_id']}
    assert api.put('/api/tasks/'+task['id'],headers=header(a),json=update).status_code==200
    assert api.put('/api/tasks/'+task['id'],headers=header(a),json=update).status_code==409
    manager_alerts=api.get('/api/notifications',headers=header(manager)).json()
    assert manager_alerts['notifications'][0]['entity_id']==task['id']
    assert api.post('/api/notifications/read-all',headers=header(a),json={}).status_code==200
    assert api.get('/api/notifications',headers=header(a)).json()['unread']==0

def test_due_reminders_are_persistent_deduplicated_and_private(db):
    a,b=users(db);api=http(db)
    task=api.post('/api/tasks',headers=header(a),json=task_body(due_at=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat())).json()
    for _ in range(3):assert api.get('/api/notifications',headers=header(a)).json()['unread']==1
    other=http(db);assert other.get('/api/notifications',headers=header(a)).json()['unread']==1
    assert not other.get('/api/notifications',headers=header(b)).json()['notifications']
    ident=api.get('/api/notifications',headers=header(a)).json()['notifications'][0]['id']
    api.post('/api/notifications/'+ident+'/read',headers=header(a),json={})
    assert api.get('/api/notifications',headers=header(a)).json()['unread']==0

@pytest.fixture
def fake_mail(monkeypatch,tmp_path):
    key=tmp_path/'host-key.pem';key.write_bytes(b'test-only-private-key')
    monkeypatch.setenv('NEXSEARCH_KEY_FILE',str(key))
    state={'messages':{},'sent':[],'seen':set(),'validity':b'7','folders':['INBOX','Sent'],'created':[],'moved':[]}
    class FakeIMAP:
        capabilities=(b'IMAP4rev1',b'MOVE')
        def __init__(self,*args,**kwargs):self.folder='INBOX'
        def login(self,login,password):
            if password!='app-password':raise imaplib.IMAP4.error('private-password-in-error')
            return 'OK',[]
        def logout(self):return 'OK',[]
        def list(self):return 'OK',[(b'(\\Sent)' if f=='Sent' else b'(\\HasNoChildren)')+b' "/" "'+f.encode('ascii')+b'"' for f in state['folders']]
        def create(self,folder):
            raw=folder.strip('"');state['created'].append(raw);state['folders'].append(raw);return 'OK',[]
        def subscribe(self,folder):return 'OK',[]
        def select(self,folder,readonly=True):self.folder=folder.strip('"');return 'OK',[b'1']
        def response(self,_):return 'UIDVALIDITY',[state['validity']]
        def uid(self,command,*args):
            if command=='search':return 'OK',[b' '.join(str(i).encode() for i in sorted(state['messages']))]
            if command=='store':
                for uid in map(int,args[0].split(',')):state['seen'].add(uid) if args[1].startswith('+') else state['seen'].discard(uid)
                return 'OK',[]
            if command=='MOVE':state['moved'].append(args);state['messages'].pop(int(args[0]),None);return 'OK',[]
            data=[]
            for uid in map(int,args[0].split(',')):
                if uid not in state['messages']:continue
                raw=state['messages'][uid]
                metadata=f'{uid} (UID {uid} FLAGS ('.encode()+(b'\\Seen' if uid in state['seen'] else b'')+f') RFC822.SIZE {len(raw)})'.encode()
                if 'HEADER.FIELDS' in args[1]:raw=raw.split(b'\n\n')[0]+b'\n\n'
                data.append((metadata,raw))
            return 'OK',data
        def append(self,*args):state['sent'].append(args[-1]);return 'OK',[]
    class FakeSMTP:
        def __init__(self,*a,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def login(self,*a):pass
        def send_message(self,message):state['sent'].append(message);return {}
    monkeypatch.setattr('network.mailbox.SecureIMAP',FakeIMAP)
    monkeypatch.setattr('network.mailbox.SecureSMTP',FakeSMTP)
    return state

def mail_bytes(subject='Запрос ставки',sender='contact@example.org'):
    message=EmailMessage();message['From']='Логистика <'+sender+'>';message['To']='me@yandex.ru';message['Subject']=subject
    message['Date']='Sat, 17 Oct 2026 12:00:00 +0300';message['Message-ID']='<test-'+str(uuid4())+'@example.org>'
    message.set_content('Добрый день! Уточните ставку.');message.add_attachment(b'demo-attachment',maintype='application',subtype='octet-stream',filename='test.txt')
    return message.as_bytes()

def connect(api,person):
    response=api.post('/api/mail/accounts',headers=header(person),json={'email':'me@yandex.ru','login':'me@yandex.ru','password':'app-password'})
    assert response.status_code==200,response.text
    return response.json()['id']

def test_personal_mail_sync_company_links_notifications_and_read(db,fake_mail):
    a,b=users(db);api=http(db);account=connect(api,a)
    company=str(db.ingest({**item(2),'emails':['contact@example.org']},a['employee']['id'])[0])
    fake_mail['messages'][1]=mail_bytes()
    sync='/api/mail/accounts/'+account+'/sync'
    assert api.post(sync,headers=header(a),json={}).json()['initial']
    assert api.get('/api/notifications',headers=header(a)).json()['unread']==0
    fake_mail['messages'][2]=mail_bytes('Новый запрос')
    assert api.post(sync,headers=header(a),json={}).status_code==200
    assert api.post(sync,headers=header(a),json={}).status_code==200
    alerts=api.get('/api/notifications',headers=header(a)).json();assert alerts['unread']==1
    items=api.get('/api/mail/messages?company_id='+company,headers=header(a)).json()['messages'];assert len(items)==2
    ident=alerts['notifications'][0]['entity_id']
    read=api.get('/api/mail/messages/'+ident,headers=header(a));assert read.status_code==200,read.text
    assert 'Уточните ставку' in read.json()['body_text'] and read.json()['attachments'][0]['name']=='test.txt'
    index=read.json()['attachments'][0]['index']
    assert api.get(f'/api/mail/messages/{ident}/attachments/{index}',headers=header(a)).content==b'demo-attachment'
    assert api.get('/api/notifications',headers=header(a)).json()['unread']==0
    assert api.patch('/api/mail/messages/'+ident,headers=header(a),json={'seen':False}).status_code==200
    assert not api.get('/api/mail/accounts',headers=header(b)).json()['accounts']
    assert api.get('/api/mail/messages/'+ident,headers=header(b)).status_code==404
    assert api.post(sync,headers=header(b),json={}).status_code==404
    assert api.get('/api/mail/messages?company_id='+company,headers=header(b)).status_code==404

def test_mail_credentials_sealed_and_director_cannot_read_another_mailbox(db,fake_mail):
    a,b,manager,director,outside=staff(db);api=http(db);ident=connect(api,a)
    public=api.get('/api/mail/accounts',headers=header(a)).text
    assert 'secret' not in public and 'app-password' not in public
    with db.transaction(admin=True) as c:row=c.execute('SELECT * FROM mail_accounts WHERE id=%s',(ident,)).fetchone()
    assert b'app-password' not in bytes(row['secret'])
    assert unseal(row['secret'],row['employee_id'],row['id'])=='app-password'
    assert not api.get('/api/mail/accounts',headers=header(director)).json()['accounts']
    assert api.delete('/api/mail/accounts/'+ident,headers=header(director)).status_code==404
    with db.transaction(director['employee']['id']) as c:assert not c.execute('SELECT * FROM mail_accounts').fetchall()
    with pytest.raises(Exception):unseal(row['secret'],b['employee']['id'],row['id'])
    invalid=api.post('/api/mail/accounts',headers=header(a),json={'email':'second@yandex.ru','login':'second','password':'very-private\npassword'})
    assert invalid.status_code==422 and 'very-private' not in invalid.text

def test_mail_send_and_disconnect_without_remote_deletion(db,fake_mail):
    a,b=users(db);api=http(db);ident=connect(api,a)
    payload={'account_id':ident,'to':['recipient@example.org'],'subject':'Проверка','text':'Учебное письмо'}
    assert api.post('/api/mail/send',headers=header(b),json=payload).status_code==404
    result=api.post('/api/mail/send',headers=header(a),json=payload)
    assert result.status_code==200 and result.json()['sent']
    assert fake_mail['sent']
    assert api.post('/api/mail/send',headers=header(a),json={**payload,'subject':'bad\nBcc: leak@example.org'}).status_code==422
    assert api.delete('/api/mail/accounts/'+ident,headers=header(a)).status_code==200
    assert not api.get('/api/mail/accounts',headers=header(a)).json()['accounts']

def test_imap_uidvalidity_reset_never_opens_a_stale_message(db,fake_mail):
    a,b=users(db);api=http(db);ident=connect(api,a);fake_mail['messages'][1]=mail_bytes()
    sync='/api/mail/accounts/'+ident+'/sync';api.post(sync,headers=header(a),json={})
    previous=api.get('/api/mail/messages',headers=header(a)).json()['messages'][0]['id']
    fake_mail['validity']=b'8';api.post(sync,headers=header(a),json={})
    assert api.get('/api/mail/messages/'+previous,headers=header(a)).status_code==404

def test_mail_older_pages_do_not_create_new_mail_notifications(db,fake_mail):
    a,b=users(db);api=http(db);ident=connect(api,a)
    for n in range(1,106):fake_mail['messages'][n]=mail_bytes('Письмо '+str(n))
    sync='/api/mail/accounts/'+ident+'/sync';api.post(sync,headers=header(a),json={})
    first=api.get('/api/mail/messages?account_id='+ident,headers=header(a)).json()['messages']
    assert len(first)==100 and min(m['uid'] for m in first)==6
    result=api.post(sync,headers=header(a),json={'before_uid':6});assert result.status_code==200,result.text
    older=api.get('/api/mail/messages?account_id='+ident+'&before_uid=6',headers=header(a)).json()['messages']
    assert len(older)==5 and max(m['uid'] for m in older)==5
    assert not api.get('/api/notifications',headers=header(a)).json()['notifications']

def test_mail_html_never_executes_and_private_hosts_are_blocked():
    message=EmailMessage();message.set_content('<p>Hello</p><script>steal()</script><img src="https://tracker.example/a"><style>hidden</style>','html')
    body,attachments=content(message);assert 'Hello' in body and 'steal' not in body and 'tracker' not in body and not attachments
    with pytest.raises(MailError):public_socket('127.0.0.1',993)
    assert folders_from_list([b'(\\Sent) "/" "Sent"'])[0]['sent'] is False  # INBOX is added first

def test_release_metadata_tracks_schema_and_excludes_local_environments(tmp_path,monkeypatch):
    import package_update,zipfile
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    monkeypatch.setattr(package_update,'ROOT',tmp_path)
    (tmp_path/'version.json').write_text(json.dumps({'version':'1.6.0','schema':16}))
    (tmp_path/'network').mkdir();(tmp_path/'network/client-trust.json').write_text('{}')
    for directory in ('company-collector','.venv','node_modules','network/.runtime'):
        p=tmp_path/directory;p.mkdir(parents=True,exist_ok=True);(p/'private.txt').write_text('private')
    (tmp_path/'.env.private').write_text('private')
    output=tmp_path/'packet.zip';package_update.package('HostUbuntu',output,Ed25519PrivateKey.generate(),'test-source')
    with zipfile.ZipFile(output) as z:
        manifest=json.loads(z.read('release.json'));assert manifest['schema']==16 and manifest['version']=='1.6.0'
        assert not any('private' in name or '.venv' in name or 'company-collector' in name for name in z.namelist())
