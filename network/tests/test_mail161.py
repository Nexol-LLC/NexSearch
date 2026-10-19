from test_crm import db,pg,users,http,header
from test_v16 import fake_mail,connect,mail_bytes,staff
from network.mailbox import encode_folder,folder_name,rich_content
from network.mail_html import sanitize_html
from network.search15 import SearchWorker
from email.message import EmailMessage
from concurrent.futures import Future
from types import SimpleNamespace
import pytest

def settings(**kw):return dict(label='Личная почта',login='me@yandex.ru',imap_host='imap.yandex.ru',smtp_host='smtp.yandex.ru',read_mode='auto',signature='С уважением, Логист',notifications=True,sync_folders=['INBOX'],**kw)

def test_settings_owned_persisted_and_credentials_never_returned(db,fake_mail):
    a,b,manager,director,_=staff(db);api=http(db);ident=connect(api,a);url='/api/mail/accounts/'+ident+'/settings'
    body=settings();body['read_mode']='html'
    for person in (b,manager,director):assert api.patch(url,headers=header(person),json=body).status_code==404
    assert api.patch(url,headers=header(a),json=body).status_code==200
    account=api.get('/api/mail/accounts',headers=header(a)).json()['accounts'][0]
    assert account['settings']['read_mode']=='html' and account['settings']['signature']==body['signature']
    assert 'password' not in account and 'secret' not in account
    assert api.patch(url,headers=header(a),json={**body,'sync_folders':['NOT-EXIST']}).status_code==400
    assert api.patch(url,headers=header(a),json={**body,'password':'private\npassword'}).status_code==422
    assert 'private\npassword' not in api.patch(url,headers=header(a),json={**body,'password':'private\npassword'}).text
    assert api.patch(url,headers=header(a),json={**body,'password':'incorrect'}).status_code==400
    assert api.patch(url,headers=header(a),json={**body,'password':'app-password'}).status_code==200

def test_create_nested_unicode_folder_is_remote_and_owner_only(db,fake_mail):
    a,b=users(db);api=http(db);ident=connect(api,a);url='/api/mail/accounts/'+ident+'/folders'
    assert folder_name(encode_folder('Клиенты & рейсы'))=='Клиенты & рейсы'
    assert api.post(url,headers=header(b),json={'name':'Чужая'}).status_code==404
    result=api.post(url,headers=header(a),json={'name':'Клиенты & рейсы'});assert result.status_code==200,result.text
    raw=result.json()['id'];assert raw in fake_mail['created'] and raw==encode_folder('Клиенты & рейсы')
    result=api.post(url,headers=header(a),json={'name':'Москва','parent':raw});assert result.status_code==200,result.text
    assert result.json()['id']==raw+'/'+encode_folder('Москва')
    assert api.post(url,headers=header(a),json={'name':'bad/name'}).status_code==400
    assert api.post(url,headers=header(a),json={'name':'bad\r\nCREATE stolen'}).status_code==400
    assert api.post(url,headers=header(a),json={'name':'Клиенты & рейсы'}).status_code==409

def test_rich_html_retains_tables_and_cid_but_no_active_or_direct_remote_content():
    message=EmailMessage();message.set_content('Текстовая версия')
    message.add_alternative('''<html><body><h1>Ставка</h1><table><tr><td style="color:#6633aa;padding:12px;background-image:url(https://tracker/a)">150 000 ₽</td></tr></table><img src="https://tracker/a" alt="Удалённое изображение"><img src="cid:logo"><a href="javascript:steal()" onclick="steal()">Ссылка</a><script>steal()</script><iframe src="https://evil/"></iframe><svg onload="steal()"></svg><form action="https://evil/"><input name="secret"></form></body></html>''',subtype='html')
    html_part=message.get_payload()[-1];html_part.add_related(b'\x89PNG\r\n\x1a\n\x00',maintype='image',subtype='png',cid='<logo>')
    text,html,attachments=rich_content(message)
    assert 'Текстовая версия' in text and '<table>' in html and 'color:#6633aa' in html and 'data:image/png;base64,' in html
    for forbidden in ('script','iframe','onload','onclick','javascript','src="https://tracker','https://evil','background-image'):
        assert forbidden not in html
    assert 'Удалённое изображение' in html
    assert 'data-mail-image="0"' in html and 'data-mail-remote="https://tracker/a"' in html
    assert 'url' not in sanitize_html('<p style="color:expression(url(test));width:999999px">x</p>')

def test_read_modes_rich_cache_unread_filter_and_bulk_flags(db,fake_mail):
    a,b=users(db);api=http(db);ident=connect(api,a)
    message=EmailMessage();message['From']='sender@example.org';message['To']='me@yandex.ru';message['Subject']='HTML';message.set_content('Текст');message.add_alternative('<p><b>Оформление</b></p>',subtype='html')
    fake_mail['messages'].update({1:message.as_bytes(),2:mail_bytes('Ещё письмо')})
    api.post('/api/mail/accounts/'+ident+'/sync',headers=header(a),json={})
    rows=api.get('/api/mail/messages?account_id='+ident,headers=header(a)).json()['messages'];ids=[r['id'] for r in rows]
    read=api.get('/api/mail/messages/'+ids[-1],headers=header(a));assert read.status_code==200,read.text
    assert 'body_html' in read.json() and read.json()['read_mode']=='auto'
    for mode in ('text','html','auto'):
        body=settings();body['read_mode']=mode
        assert api.patch('/api/mail/accounts/'+ident+'/settings',headers=header(a),json=body).status_code==200
        assert api.get('/api/mail/messages/'+ids[-1],headers=header(a)).json()['read_mode']==mode
    url='/api/mail/accounts/'+ident+'/mark'
    assert api.post(url,headers=header(b),json={'ids':ids,'seen':True}).status_code==404
    assert api.post(url,headers=header(a),json={'ids':ids,'seen':True}).json()['updated']==2
    assert api.get('/api/mail/messages?account_id='+ident+'&unread=true',headers=header(a)).json()['messages']==[]
    assert api.post(url,headers=header(a),json={'ids':ids,'seen':False}).status_code==200
    assert len(api.get('/api/mail/messages?account_id='+ident+'&unread=true',headers=header(a)).json()['messages'])==2
    account=api.get('/api/mail/accounts',headers=header(a)).json()['accounts'][0];assert account['folder_counts']['INBOX']['unread']==2

def test_notifications_can_be_disabled_and_message_moves_safely(db,fake_mail):
    a,b=users(db);api=http(db);ident=connect(api,a);url='/api/mail/accounts/'+ident
    api.post(url+'/sync',headers=header(a),json={})
    body=settings();body['notifications']=False
    api.patch(url+'/settings',headers=header(a),json=body);fake_mail['messages'][1]=mail_bytes()
    api.post(url+'/sync',headers=header(a),json={})
    assert not api.get('/api/notifications',headers=header(a)).json()['notifications']
    mid=api.get('/api/mail/messages?account_id='+ident,headers=header(a)).json()['messages'][0]['id']
    assert api.post('/api/mail/messages/'+mid+'/move',headers=header(b),json={'folder':'Sent'}).status_code==404
    result=api.post('/api/mail/messages/'+mid+'/move',headers=header(a),json={'folder':'Sent'});assert result.status_code==200,result.text
    assert fake_mail['moved'] and api.get('/api/mail/messages/'+mid,headers=header(a)).status_code==404

def test_scheduler_checks_each_minute_without_overlapping_accounts(db,fake_mail):
    a,b=users(db);api=http(db);ident=connect(api,a);mail=api.app.state.mailbox
    jobs=[]
    class Pool:
        def submit(self,fn,account):
            future=Future();jobs.append((fn,account,future));return future
    mail.pool=Pool();mail.tick(100);assert len(jobs)==1
    mail.tick(160);assert len(jobs)==1  # An unfinished request is never duplicated.
    jobs[-1][2].set_result(None);mail.tick(160);assert len(jobs)==2
    jobs[-1][2].set_result(None);mail.tick(219);assert len(jobs)==2
    mail.tick(220);assert len(jobs)==3
    called=[];mail.sync=lambda owner,account,folder:called.append(folder)
    mail.sync_account({'employee_id':a['employee']['id'],'id':ident,'settings':{'sync_folders':['INBOX','Sent']}})
    assert called==['INBOX','Sent']

def test_search_coordinator_recovers_after_transient_failure():
    worker=SearchWorker(SimpleNamespace());calls=[]
    def serve():
        calls.append(1)
        if len(calls)==1:raise OSError('Transient socket failure')
        worker.closed.set()
    worker._serve_once=serve;worker.loop();assert len(calls)==2


def test_schema16_upgrade_preserves_accounts_messages_and_employees(db,fake_mail):
    a,b=users(db);api=http(db);ident=connect(api,a)
    fake_mail['messages'][1]=mail_bytes('Существующее письмо')
    api.post('/api/mail/accounts/'+ident+'/sync',headers=header(a),json={})
    with db.transaction(admin=True) as c:
        c.execute('ALTER TABLE mail_accounts DROP COLUMN settings')
        c.execute('ALTER TABLE mail_messages DROP COLUMN body_html,DROP COLUMN content_version')
        c.execute('DELETE FROM schema_meta WHERE version>=17')
    db.initialize();db.initialize()  # Both the real upgrade and a repeated startup.
    accounts=api.get('/api/mail/accounts',headers=header(a)).json()['accounts']
    assert accounts[0]['id']==ident and accounts[0]['settings']=={}
    rows=api.get('/api/mail/messages?account_id='+ident,headers=header(a)).json()['messages']
    assert len(rows)==1 and rows[0]['subject']=='Существующее письмо'
    assert api.get('/api/me',headers=header(a)).status_code==200
    with db.transaction(admin=True) as c:assert c.execute('SELECT 1 FROM schema_meta WHERE version=17').fetchone()


def test_151_privileged_updater_finishes_migration_before_readonly_runtime(db):
    from pathlib import Path
    from uuid import uuid4
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from network import secure_store
    from network.database import Database
    root=Path(__file__).resolve().parents[2]
    cfg=secure_store.read(root/'network/.runtime/test-host/postgres.sec')
    admin_dsn=make_conninfo(host='127.0.0.1',port=cfg['port'],user='nexol_admin',password=cfg['admin_password'],dbname='nexol')
    role='migration_reader_'+uuid4().hex[:12]
    with db.transaction(admin=True) as c:
        c.execute('ALTER TABLE mail_accounts DROP COLUMN settings')
        c.execute('ALTER TABLE mail_messages DROP COLUMN body_html,DROP COLUMN content_version')
        c.execute('DELETE FROM schema_meta WHERE version>=17')
        # This is the exact migration list in the deployed 1.5.1 updater.
        for name in ('schema14.sql','schema15.sql'):c.execute((root/'network'/name).read_text(encoding='utf-8'))
    with psycopg.connect(admin_dsn,autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
        try:
            admin.execute(sql.SQL('GRANT {} TO nexol_app').format(sql.Identifier(role)))
            admin.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
            admin.execute(sql.SQL('GRANT SELECT ON schema_meta TO {}').format(sql.Identifier(role)))
            reader=Database(make_conninfo(db.pool.conninfo,options='-c role='+role))
            try:
                reader.initialize()  # Must finish without any DDL permissions.
                with reader.transaction() as c:
                    assert c.execute('SELECT 1 FROM schema_meta WHERE version=17').fetchone()
                    assert not c.execute("SELECT has_table_privilege(current_user,'mail_accounts','UPDATE') AS allowed").fetchone()['allowed']
            finally:reader.close()
        finally:
            admin.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_cached_old_letter_still_opens_when_imap_unavailable(db,fake_mail,monkeypatch):
    from network.mailbox import MailError
    a,b=users(db);api=http(db);ident=connect(api,a)
    fake_mail['messages'][1]=mail_bytes('Сохранённое письмо')
    api.post('/api/mail/accounts/'+ident+'/sync',headers=header(a),json={})
    mid=api.get('/api/mail/messages?account_id='+ident,headers=header(a)).json()['messages'][0]['id']
    with db.transaction(admin=True) as c:
        c.execute('UPDATE mail_messages SET body_text=%s,content_version=0 WHERE id=%s',('Сохранённый текст',mid))
    def unavailable(*args):raise MailError('Временно недоступно')
    monkeypatch.setattr(api.app.state.mailbox,'raw_message',unavailable)
    monkeypatch.setattr(api.app.state.mailbox,'mark',unavailable)
    response=api.get('/api/mail/messages/'+mid,headers=header(a))
    assert response.status_code==200,response.text
    assert response.json()['body_text']=='Сохранённый текст' and response.json()['warning'] and not response.json()['seen']


def test_search_reacquires_real_postgres_lease_after_disconnection(db):
    import time
    from uuid import uuid4
    a,b=users(db);owner=a['employee']['id'];worker=SearchWorker(db)
    def finish(job):
        with db.transaction(admin=True) as c:c.execute("UPDATE jobs SET status='completed',finished_at=now() WHERE id=%s",(job['id'],))
    worker.run=finish
    def lease_pid():
        with db.transaction(admin=True) as c:
            row=c.execute("SELECT pid FROM pg_locks WHERE locktype='advisory' AND objid=73142003 AND granted").fetchone()
            return row['pid'] if row else None
    worker.start()
    try:
        deadline=time.monotonic()+5;old=None
        while time.monotonic()<deadline and not old:old=lease_pid();time.sleep(.05)
        assert old
        with db.transaction(admin=True) as c:assert c.execute('SELECT pg_terminate_backend(%s) AS stopped',(old,)).fetchone()['stopped']
        deadline=time.monotonic()+12;new=None
        while time.monotonic()<deadline:
            new=lease_pid()
            if new and new!=old:break
            time.sleep(.1)
        assert new and new!=old
        ident=uuid4()
        with db.transaction(admin=True) as c:c.execute("INSERT INTO jobs(id,employee_id,target,status,criteria) VALUES(%s,%s,1,'queued','{}')",(ident,owner))
        worker.wake.set();deadline=time.monotonic()+5
        while time.monotonic()<deadline and worker.job(ident)['status']!='completed':time.sleep(.05)
        assert worker.job(ident)['status']=='completed'
    finally:worker.stop()
