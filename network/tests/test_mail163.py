from test_crm import db,pg,users,http,header
from test_v16 import fake_mail,connect,mail_bytes,staff
from network.mailbox import folders_from_list,rich_content,MailConnectionError,MailAuthenticationError,public_socket
from network.mail_images import download_image,ImageUnavailable
from network.mail_html import sanitize_html
from email.message import EmailMessage
from psycopg.types.json import Jsonb
from uuid import uuid4
import imaplib,base64,json
import pytest

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')

def test_inbox_first_even_after_the_folder_limit_and_noselect_parents_retained():
    lines=[b'(\\Noselect) "/" "Clients"']+[f'(\\HasNoChildren) "/" "Other{i}"'.encode() for i in range(110)]+[b'(\\HasNoChildren) "/" "INBOX"']
    folders=folders_from_list(lines)
    assert folders[0]['id']=='INBOX' and len(folders)==100
    assert next(f for f in folders if f['id']=='Clients')['selectable'] is False

def test_cid_with_escaped_id_and_filename_and_embedded_data_image():
    m=EmailMessage();m.set_content('text')
    uri='data:image/png;base64,'+base64.b64encode(PNG).decode()
    m.add_alternative('<img src="cid:logo%40example"><img src="'+uri+'"><img src="data:image/svg+xml;base64,PHN2Zz4=">',subtype='html')
    m.get_payload()[-1].add_related(PNG,maintype='image',subtype='png',cid='<logo@example>',filename='logo.png')
    _,html,_=rich_content(m)
    assert html.count('src="data:image/png;base64,')==2 and 'svg' not in html
    assert 'data-mail-image' in sanitize_html('<img src="//cdn.example.org/image.png">')

def test_imap_abort_reconnects_without_disabling_account_or_password_error(db,fake_mail,monkeypatch):
    import network.mailbox as module
    a,b=users(db);api=http(db);ident=connect(api,a);fake_mail['messages'][1]=mail_bytes()
    original=module.SecureIMAP;attempts=[]
    class Flaky(original):
        def __init__(self,*args,**kw):super().__init__(*args,**kw);attempts.append(1)
        def uid(self,command,*args):
            if command=='fetch' and len(attempts)==1:raise imaplib.IMAP4.abort('socket EOF')
            return super().uid(command,*args)
    monkeypatch.setattr(module,'SecureIMAP',Flaky)
    result=api.post('/api/mail/accounts/'+ident+'/sync',headers=header(a),json={})
    assert result.status_code==200,result.text
    assert len(attempts)==2
    account=api.get('/api/mail/accounts',headers=header(a)).json()['accounts'][0]
    assert account['enabled'] and account['last_error']==''

def test_authentication_failure_is_not_retried_or_echoed(db,fake_mail,monkeypatch):
    import network.mailbox as module
    a,b=users(db);api=http(db);ident=connect(api,a);attempts=[]
    class Denied(module.SecureIMAP):
        def login(self,*args):attempts.append(1);raise imaplib.IMAP4.error('[AUTHENTICATIONFAILED] secret-pass-do-not-echo')
    monkeypatch.setattr(module,'SecureIMAP',Denied)
    response=api.post('/api/mail/accounts/'+ident+'/sync',headers=header(a),json={})
    assert response.status_code==400 and len(attempts)==1 and 'secret-pass' not in response.text
    assert api.get('/api/mail/accounts',headers=header(a)).json()['accounts'][0]['enabled']

def test_temporary_login_limit_is_retried(db,fake_mail,monkeypatch):
    import network.mailbox as module
    a,b=users(db);api=http(db);ident=connect(api,a);attempts=[]
    class Limited(module.SecureIMAP):
        def login(self,*args):
            attempts.append(1)
            if len(attempts)==1:raise imaplib.IMAP4.error('[UNAVAILABLE] too many connections')
            return super().login(*args)
    monkeypatch.setattr(module,'SecureIMAP',Limited)
    assert api.post('/api/mail/accounts/'+ident+'/sync',headers=header(a),json={}).status_code==200
    assert len(attempts)==2

def test_transient_failure_exhaustion_still_opens_cached_html(db,fake_mail,monkeypatch):
    import network.mailbox as module
    a,b=users(db);api=http(db);ident=connect(api,a);fake_mail['messages'][1]=mail_bytes()
    api.post('/api/mail/accounts/'+ident+'/sync',headers=header(a),json={})
    mid=api.get('/api/mail/messages?account_id='+ident,headers=header(a)).json()['messages'][0]['id']
    with db.transaction(admin=True) as c:c.execute("UPDATE mail_messages SET body_text='cached',body_html='<b>cached HTML</b>',content_version=1,seen=true WHERE id=%s",(mid,))
    class Offline(module.SecureIMAP):
        def __init__(self,*args,**kw):raise imaplib.IMAP4.abort('EOF')
    monkeypatch.setattr(module,'SecureIMAP',Offline)
    result=api.get('/api/mail/messages/'+mid,headers=header(a));assert result.status_code==200
    assert result.json()['body_html']=='<b>cached HTML</b>' and result.json()['warning']

def test_remote_image_endpoint_is_private_even_for_director(db,fake_mail,monkeypatch):
    a,b,manager,director,_=staff(db);api=http(db);ident=connect(api,a);fake_mail['messages'][1]=mail_bytes()
    api.post('/api/mail/accounts/'+ident+'/sync',headers=header(a),json={})
    mid=api.get('/api/mail/messages?account_id='+ident,headers=header(a)).json()['messages'][0]['id']
    with db.transaction(admin=True) as c:c.execute('UPDATE mail_messages SET body_html=%s,content_version=2 WHERE id=%s',(sanitize_html('<img src="https://cdn.example.org/a.png">'),mid))
    calls=[]
    def raster(owner,url,connector):calls.append((owner,url));return PNG,'image/png'
    monkeypatch.setattr(api.app.state.mailbox.images,'get',raster)
    path='/api/mail/messages/'+mid+'/images/0'
    for person in [b,manager,director]:assert api.get(path,headers=header(person)).status_code==404
    assert not calls
    response=api.get(path,headers=header(a));assert response.content==PNG and response.headers['content-type']=='image/png'
    assert response.headers['cache-control'] in ('no-store','private, max-age=600') and len(calls)==1
    assert api.get(path[:-1]+'99',headers=header(a)).status_code==404

@pytest.mark.parametrize('kind',['html','oversize','redirect-private','svg'])
def test_image_proxy_rejects_active_oversized_and_private_redirects(monkeypatch,kind):
    import network.mail_images as module
    requests=[]
    class Reply:
        status=302 if kind=='redirect-private' else 200
        def getheader(self,name,default=None):return {'Location':'http://127.0.0.1/private','Content-Length':str(3*1024**2) if kind=='oversize' else None}.get(name,default)
        def read(self,size):assert kind!='oversize';return b'<svg onload="attack()"/>' if kind=='svg' else b'<html>attack()</html>'
    class HTTP:
        def __init__(self,*args,**kw):pass
        def request(self,method,path,headers):requests.append(headers);assert 'Cookie' not in headers and 'Authorization' not in headers
        def getresponse(self):return Reply()
        def close(self):pass
    monkeypatch.setattr(module.http.client,'HTTPConnection',HTTP)
    def connector(host,*args,**kw):
        if host=='127.0.0.1':return public_socket(host,*args,**kw)
        return object()
    with pytest.raises(ImageUnavailable):download_image('http://cdn.example.org/image',connector)
    assert len(requests)==1

def test_related_sent_messages_are_account_and_correspondent_scoped(db,fake_mail):
    a,b=users(db);api=http(db);ident=connect(api,a);fake_mail['messages'][1]=mail_bytes()
    api.post('/api/mail/accounts/'+ident+'/sync',headers=header(a),json={});owner=a['employee']['id']
    with db.transaction(owner) as c:
        for target,subject in [('contact@example.org','Our reply'),('unrelated@example.org','Unrelated')]:
            c.execute('''INSERT INTO mail_messages(id,employee_id,account_id,folder,uidvalidity,uid,subject,sender,recipients,addresses,in_reply_to,reference_ids)
             VALUES(%s,%s,%s,'Sent','7',%s,%s,'me@yandex.ru',%s,%s,'<root@example.org>',ARRAY['<root@example.org>'])''',(uuid4(),owner,ident,2 if subject=='Our reply' else 3,subject,Jsonb([target]),['me@yandex.ru',target]))
    result=api.get('/api/mail/messages?account_id='+ident,headers=header(a)).json()
    assert [m['subject'] for m in result['related']]==['Our reply']
    assert result['related'][0]['in_reply_to']=='<root@example.org>'
    assert api.get('/api/mail/messages?account_id='+ident,headers=header(b)).json()=={'messages':[],'related':[]}

def test_schema18_preserves_mail_and_supports_old_privileged_updater(db,fake_mail):
    from pathlib import Path
    a,b=users(db);api=http(db);ident=connect(api,a);fake_mail['messages'][1]=mail_bytes()
    api.post('/api/mail/accounts/'+ident+'/sync',headers=header(a),json={})
    with db.transaction(admin=True) as c:
        c.execute('ALTER TABLE mail_messages DROP COLUMN in_reply_to,DROP COLUMN reference_ids');c.execute('DELETE FROM schema_meta WHERE version=18')
    db.initialize();assert len(api.get('/api/mail/messages?account_id='+ident,headers=header(a)).json()['messages'])==1
    with db.transaction(admin=True) as c:
        assert c.execute('SELECT 1 FROM schema_meta WHERE version=18').fetchone()
        c.execute('ALTER TABLE mail_messages DROP COLUMN in_reply_to,DROP COLUMN reference_ids');c.execute('DELETE FROM schema_meta WHERE version=18')
        c.execute((Path(__file__).resolve().parents[1]/'schema17.sql').read_text(encoding='utf-8'))
        assert c.execute('SELECT 1 FROM schema_meta WHERE version=18').fetchone()
        assert c.execute('SELECT reference_ids FROM mail_messages').fetchone()['reference_ids']==[]

def test_connections_are_bounded_and_serialized_per_account(monkeypatch):
    import network.mailbox as module
    import threading,time
    from types import SimpleNamespace
    from concurrent.futures import ThreadPoolExecutor
    class Client:
        def __init__(self,*args,**kw):pass
        def login(self,*args):pass
        def logout(self):pass
        def shutdown(self):pass
    monkeypatch.setattr(module,'SecureIMAP',Client);monkeypatch.setattr(module,'unseal',lambda *args:'test-only')
    box=module.Mailbox(SimpleNamespace());guard=threading.Lock();active={};maximum=[0]
    accounts=[dict(id=str(i),employee_id='owner',imap_host='imap.example.org',imap_port=993,login='test',secret=b'') for i in range(8)]
    def operation(account):
        with box.connection(account):
            with guard:
                active[account['id']]=active.get(account['id'],0)+1
                assert active[account['id']]==1
                maximum[0]=max(maximum[0],sum(active.values()))
            time.sleep(.03)
            with guard:active[account['id']]-=1
    with ThreadPoolExecutor(max_workers=10) as pool:list(pool.map(operation,[accounts[0],accounts[0],*accounts[1:]]))
    assert maximum[0]<=3 and sum(active.values())==0
