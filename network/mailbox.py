"""Personal IMAP/SMTP mail. Passwords are sealed; HTML and remote images never run."""
import base64
import hashlib
import imaplib
import ipaddress
import json
import os
import re
import smtplib
import socket
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime, formatdate, make_msgid
from html.parser import HTMLParser
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from psycopg.types.json import Jsonb
from .database import NotFound, Conflict
from .host_secrets import key_path
from .notifications import notify

MAX_MESSAGE=15*1024*1024
ACCOUNT_FIELDS='id,email,login,label,imap_host,imap_port,smtp_host,smtp_port,enabled,folders,last_sync_at,last_error'
MESSAGE_FIELDS='id,account_id,folder,uid,subject,sender,recipients,addresses,sent_at,seen,size_bytes,message_id'

class MailError(ValueError):pass

def _key():
    try:raw=key_path().read_bytes()
    except OSError:raise MailError('На хосте не настроено защищённое хранилище почты') from None
    return hashlib.sha256(raw+b'NexSearch personal mail 1.6').digest()

def seal(password,owner,account):
    nonce=os.urandom(12)
    return nonce+AESGCM(_key()).encrypt(nonce,password.encode(),f'{owner}:{account}'.encode())

def unseal(value,owner,account):
    value=bytes(value)
    return AESGCM(_key()).decrypt(value[:12],value[12:],f'{owner}:{account}'.encode()).decode()

def public_socket(host,port,timeout=12):
    """Resolve once and connect to that public IP, retaining TLS hostname checks."""
    try:addresses=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
    except OSError:raise MailError('Почтовый сервер не найден') from None
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise MailError('Укажите публичный почтовый сервер с TLS')
    last=None
    for family,kind,proto,_,address in addresses:
        sock=socket.socket(family,kind,proto);sock.settimeout(timeout)
        try:sock.connect(address);return sock
        except OSError as error:last=error;sock.close()
    raise MailError('Почтовый сервер не отвечает') from last

class SecureIMAP(imaplib.IMAP4_SSL):
    max_literal=256*1024
    def read(self,size):
        if size>self.max_literal:raise MailError('Почтовый сервер прислал слишком большой блок данных')
        return super().read(size)
    def _create_socket(self,timeout):
        sock=public_socket(self.host,self.port,timeout or 12)
        try:return self.ssl_context.wrap_socket(sock,server_hostname=self.host)
        except BaseException:sock.close();raise

class SecureSMTP(smtplib.SMTP_SSL):
    def _get_socket(self,host,port,timeout):
        sock=public_socket(host,port,timeout or 12)
        try:return self.context.wrap_socket(sock,server_hostname=host)
        except BaseException:sock.close();raise

def folder_name(value):
    def replace(match):
        encoded=match.group(1)
        if not encoded:return '&'
        try:return base64.b64decode(encoded.replace(',','/')+'='*((-len(encoded))%4)).decode('utf-16-be')
        except (ValueError,UnicodeError):return match.group(0)
    return re.sub(r'&([A-Za-z0-9+,]*)-',replace,value)

def folders_from_list(lines):
    result=[]
    for line in lines or []:
        if not isinstance(line,bytes):continue
        match=re.match(rb'\(([^)]*)\)\s+(?:"(?:[^"\\]|\\.)*"|NIL)\s+(.+)$',line)
        if not match:continue
        flags=match[1].decode('ascii','ignore').lower()
        if '\\noselect' in flags:continue
        raw=match[2].decode('ascii','replace')
        if raw.startswith('"') and raw.endswith('"'):raw=re.sub(r'\\(.)',r'\1',raw[1:-1])
        if len(raw)>512 or any(ord(c)<32 for c in raw):continue
        display=folder_name(raw)
        sent='\\sent' in flags or display.casefold() in ('sent','sent items','sent messages','отправленные')
        result.append({'id':raw,'name':display,'sent':sent})
    if not any(f['id'].upper()=='INBOX' for f in result):result.insert(0,{'id':'INBOX','name':'Входящие','sent':False})
    return result[:100]

def select_folder(client,folder,readonly=True):
    if len(folder)>512 or any(ord(c)<32 for c in folder):raise MailError('Недопустимая папка')
    safe='"'+folder.replace('\\','\\\\').replace('"','\\"')+'"'
    status,_=client.select(safe,readonly=readonly)
    if status!='OK':raise MailError('Почтовая папка недоступна')
    status,values=client.response('UIDVALIDITY')
    validity=(values or [b''])[0]
    if not validity or not validity.isdigit():raise MailError('Сервер не сообщил идентификатор папки')
    return validity.decode()

class TextHTML(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.parts=[];self.blocked=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','head'):self.blocked+=1
        if not self.blocked and tag in ('p','div','br','tr','li'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','head') and self.blocked:self.blocked-=1
    def handle_data(self,data):
        if not self.blocked:self.parts.append(data)

def content(message):
    plain=[];html=[];attachments=[]
    for index,part in enumerate(message.walk()):
        if part.is_multipart():continue
        filename=part.get_filename()
        if filename or part.get_content_disposition()=='attachment':
            attachments.append({'index':index,'name':str(filename or 'Вложение')[:300],'type':part.get_content_type(),'size':len(part.get_payload(decode=True) or b'')});continue
        if part.get_content_type() not in ('text/plain','text/html'):continue
        try:value=part.get_content()
        except (LookupError,UnicodeError):value=(part.get_payload(decode=True) or b'').decode('utf-8','replace')
        (plain if part.get_content_type()=='text/plain' else html).append(str(value))
    if plain:body='\n'.join(plain)
    else:
        parser=TextHTML();parser.feed('\n'.join(html));body=''.join(parser.parts)
    return body[:1_000_000],attachments

def headers(raw,metadata):
    message=BytesParser(policy=policy.default).parsebytes(raw,headersonly=True)
    senders=getaddresses(message.get_all('from',[]));recipients=getaddresses(message.get_all('to',[])+message.get_all('cc',[]))
    addresses=list(dict.fromkeys(address.lower() for _,address in senders+recipients if address))
    try:date=parsedate_to_datetime(str(message.get('date','')));date=date if date.tzinfo else date.replace(tzinfo=timezone.utc)
    except (TypeError,ValueError,OverflowError):date=None
    return {'subject':str(message.get('subject','Без темы'))[:1000],'sender':str(message.get('from',''))[:1000],
      'recipients':[a for _,a in recipients],'addresses':addresses,'sent_at':date,'message_id':str(message.get('message-id',''))[:1000],
      'seen':b'\\Seen' in metadata,'size_bytes':int((re.search(rb'RFC822.SIZE (\d+)',metadata) or [b'',b'0'])[1])}

class Mailbox:
    def __init__(self,db):
        self.db=db;self.stopping=threading.Event();self.thread=None
        self.locks=[threading.Lock() for _ in range(64)];self.slots=threading.BoundedSemaphore(3)

    @contextmanager
    def connection(self,account):
        client=None
        try:
            client=SecureIMAP(account['imap_host'],account['imap_port'],ssl_context=ssl.create_default_context(),timeout=12)
            client.login(account['login'],unseal(account['secret'],account['employee_id'],account['id']))
            yield client
        except imaplib.IMAP4.error:raise MailError('Не удалось войти в почту. Проверьте пароль приложения и доступ по IMAP.') from None
        except (OSError,ssl.SSLError,UnicodeError):raise MailError('Не удалось установить защищённое соединение с почтой') from None
        except InvalidTag:raise MailError('Защищённый пароль недоступен. Подключите почтовый ящик заново.') from None
        finally:
            if client:
                try:client.logout()
                except Exception:pass

    def account(self,owner,ident):
        with self.db.transaction(owner) as c:
            row=c.execute('SELECT * FROM mail_accounts WHERE id=%s AND employee_id=%s AND enabled',(ident,owner)).fetchone()
        if not row:raise NotFound('Почтовый аккаунт недоступен')
        return row

    def connect(self,owner,body):
        ident=uuid4();values=body.model_dump(exclude={'password'})
        account={**values,'employee_id':owner,'id':ident,'secret':seal(body.password,owner,ident)}
        with self.connection(account) as client:
            status,lines=client.list()
            if status!='OK':raise MailError('Не удалось получить папки почты')
            folders=folders_from_list(lines)
        with self.db.transaction(owner) as c:
            if c.execute('SELECT 1 FROM mail_accounts WHERE employee_id=%s AND email=%s',(owner,body.email)).fetchone():
                raise Conflict('Этот ящик уже подключён. Для смены пароля отключите его и подключите снова.')
            c.execute('''INSERT INTO mail_accounts(id,employee_id,email,login,label,imap_host,imap_port,smtp_host,smtp_port,secret,folders)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
              (ident,owner,body.email,body.login,body.label,body.imap_host,body.imap_port,body.smtp_host,body.smtp_port,account['secret'],Jsonb(folders)))
        self.db.audit(owner,'mail.connect',ident,{'email':body.email})
        return ident

    def sync(self,owner,ident,folder='INBOX',before_uid=None):
        lock=self.locks[hash(str(ident))%len(self.locks)]
        if not lock.acquire(blocking=False):raise Conflict('Почта уже синхронизируется')
        try:
            if not self.slots.acquire(blocking=False):raise Conflict('Почтовые подключения заняты. Повторите через несколько секунд.')
            try:return self._sync(owner,ident,folder,before_uid)
            finally:self.slots.release()
        except MailError as error:
            with self.db.transaction(owner) as c:c.execute('UPDATE mail_accounts SET last_error=%s WHERE id=%s AND employee_id=%s',(str(error),ident,owner))
            raise
        finally:lock.release()

    def _sync(self,owner,ident,folder,before_uid=None):
        account=self.account(owner,ident)
        with self.connection(account) as client:
            status,lines=client.list();folders=folders_from_list(lines) if status=='OK' else account['folders']
            if folder not in [f['id'] for f in folders]:raise MailError('Папка недоступна')
            validity=select_folder(client,folder)
            status,values=client.uid('search',None,'ALL')
            if status!='OK':raise MailError('Не удалось получить список писем')
            uids=[int(u) for u in (values[0] or b'').split() if u.isdigit()]
            batch=[uid for uid in uids if before_uid is None or uid<before_uid][-100:];fetched=[]
            if batch:
                status,data=client.uid('fetch',','.join(map(str,batch)),'(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO CC DATE MESSAGE-ID)])')
                if status!='OK':raise MailError('Не удалось загрузить заголовки писем')
                for value in data:
                    if not isinstance(value,tuple):continue
                    metadata,raw=value;match=re.search(rb'\bUID (\d+)',metadata)
                    if match:fetched.append((int(match[1]),headers(raw,metadata)))
        previous=(account['sync_state'] or {}).get(folder,{})
        initial=previous.get('uidvalidity')!=validity
        highest=previous.get('last_uid',0) if not initial else 0
        with self.db.transaction(owner) as c:
            c.execute('DELETE FROM mail_messages WHERE account_id=%s AND employee_id=%s AND folder=%s AND uidvalidity<>%s',(ident,owner,folder,validity))
            cached=c.execute('SELECT id,uid FROM mail_messages WHERE account_id=%s AND employee_id=%s AND folder=%s',(ident,owner,folder)).fetchall()
            active=set(uids)
            for old in cached:
                if old['uid'] not in active:c.execute('DELETE FROM mail_messages WHERE id=%s',(old['id'],))
            for uid,item in fetched:
                row=c.execute('''INSERT INTO mail_messages(id,employee_id,account_id,folder,uidvalidity,uid,subject,sender,recipients,addresses,sent_at,seen,size_bytes,message_id)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                  ON CONFLICT(account_id,folder,uidvalidity,uid) DO UPDATE SET seen=excluded.seen
                  RETURNING id''',(uuid4(),owner,ident,folder,validity,uid,item['subject'],item['sender'],Jsonb(item['recipients']),item['addresses'],item['sent_at'],item['seen'],item['size_bytes'],item['message_id'])).fetchone()
                if not initial and folder.upper()=='INBOX' and uid>highest and not item['seen']:
                    notify(c,owner,'mail.new',item['subject'],item['sender'],row['id'],f'mail:{ident}:{folder}:{validity}:{uid}')
            state={**(account['sync_state'] or {}),folder:{'uidvalidity':validity,'last_uid':max(uids,default=highest)}}
            c.execute("UPDATE mail_accounts SET folders=%s,sync_state=%s,last_sync_at=now(),last_error='' WHERE id=%s AND employee_id=%s",(Jsonb(folders),Jsonb(state),ident,owner))
        self.db.events.publish(owner,'mail.sync')
        return {'loaded':len(fetched),'folder':folder,'initial':initial}

    def message(self,owner,ident):
        with self.db.transaction(owner) as c:
            row=c.execute('SELECT * FROM mail_messages WHERE id=%s AND employee_id=%s',(ident,owner)).fetchone()
        if not row:raise NotFound('Письмо недоступно')
        return row

    def raw_message(self,owner,row):
        if row['size_bytes']>MAX_MESSAGE:raise MailError('Письмо больше 15 МБ. Откройте его в почте провайдера.')
        account=self.account(owner,row['account_id'])
        with self.connection(account) as client:
            client.max_literal=MAX_MESSAGE
            if select_folder(client,row['folder'])!=row['uidvalidity']:raise Conflict('Почтовая папка изменилась. Синхронизируйте почту.')
            status,values=client.uid('fetch',str(row['uid']),'(BODY.PEEK[])')
            raw=next((v[1] for v in values or [] if isinstance(v,tuple)),None)
            if status!='OK' or raw is None:raise NotFound('Письмо удалено или недоступно на сервере')
            if len(raw)>MAX_MESSAGE:raise MailError('Письмо больше 15 МБ')
        return BytesParser(policy=policy.default).parsebytes(raw)

    def read(self,owner,ident):
        row=self.message(owner,ident)
        if row['body_text'] is None:
            body,attachments=content(self.raw_message(owner,row))
            with self.db.transaction(owner) as c:c.execute('UPDATE mail_messages SET body_text=%s,attachments=%s WHERE id=%s AND employee_id=%s',(body,Jsonb(attachments),ident,owner))
            row['body_text']=body;row['attachments']=attachments
        if not row['seen']:self.mark(owner,ident,True)
        return {**{k:v for k,v in row.items() if k not in ('employee_id','uid','uidvalidity')},'seen':True}

    def mark(self,owner,ident,seen):
        row=self.message(owner,ident);account=self.account(owner,row['account_id'])
        with self.connection(account) as client:
            if select_folder(client,row['folder'],False)!=row['uidvalidity']:raise Conflict('Синхронизируйте папку заново')
            status,_=client.uid('store',str(row['uid']),'+FLAGS.SILENT' if seen else '-FLAGS.SILENT','(\\Seen)')
            if status!='OK':raise MailError('Не удалось изменить отметку прочтения')
        with self.db.transaction(owner) as c:
            c.execute('UPDATE mail_messages SET seen=%s WHERE id=%s AND employee_id=%s',(seen,ident,owner))
            if seen:c.execute("UPDATE notifications SET read_at=COALESCE(read_at,now()) WHERE employee_id=%s AND kind='mail.new' AND entity_id=%s",(owner,str(ident)))

    def attachment(self,owner,ident,index):
        row=self.message(owner,ident);message=self.raw_message(owner,row)
        parts=list(message.walk())
        if index<0 or index>=len(parts):raise NotFound('Вложение не найдено')
        part=parts[index]
        if not part.get_filename() and part.get_content_disposition()!='attachment':raise NotFound('Вложение не найдено')
        return str(part.get_filename() or 'attachment'),part.get_payload(decode=True) or b''

    def send(self,owner,body):
        account=self.account(owner,body.account_id)
        if not account['smtp_host']:raise MailError('Исходящая почта для этого ящика не настроена')
        message=EmailMessage();message['From']=account['email'];message['To']=', '.join(body.to)
        if body.cc:message['Cc']=', '.join(body.cc)
        message['Subject']=body.subject;message['Date']=formatdate(localtime=False);message['Message-ID']=make_msgid()
        if body.reply_to:
            previous=self.message(owner,body.reply_to)
            if previous['message_id']:message['In-Reply-To']=previous['message_id'];message['References']=previous['message_id']
        message.set_content(body.text)
        warning=''
        try:
            with SecureSMTP(account['smtp_host'],account['smtp_port'],timeout=12,context=ssl.create_default_context()) as client:
                client.login(account['login'],unseal(account['secret'],owner,account['id']))
                refused=client.send_message(message)
                if refused:warning='Письмо отправлено не всем: '+str(len(refused))+' адресатов отклонили доставку. Не отправляйте повторно всем получателям.'
        except (smtplib.SMTPException,OSError):raise MailError('Сервер не подтвердил отправку. Проверьте SMTP, пароль приложения и папку отправленных перед повторной попыткой.') from None
        self.db.audit(owner,'mail.send',account['id'],{'recipients':len(body.to)+len(body.cc)})
        # Do not resend if saving a sent copy fails after successful SMTP delivery.
        sent=next((f['id'] for f in account['folders'] if f.get('sent')),None)
        if sent:
            try:
                with self.connection(account) as client:
                    status,_=client.append('"'+sent.replace('"','\\"')+'"','(\\Seen)',imaplib.Time2Internaldate(datetime.now(timezone.utc)),message.as_bytes())
                    if status!='OK':warning+=' Письмо отправлено, но копия в папке отправленных не сохранена.'
            except MailError:warning+=' Письмо отправлено, но копия в папке отправленных не сохранена.'
        else:warning+=' Письмо отправлено. Папка отправленных не определена сервером.'
        return {'sent':True,'warning':warning}

    def start(self):
        if self.thread:return
        self.thread=threading.Thread(target=self.run,name='personal-mail-sync',daemon=True);self.thread.start()

    def run(self):
        while not self.stopping.wait(60):
            with self.db.transaction(admin=True) as c:
                accounts=c.execute('SELECT a.id,a.employee_id FROM mail_accounts a JOIN employees e ON e.id=a.employee_id WHERE a.enabled AND e.active AND e.approved').fetchall()
            def sync_account(account):
                if self.stopping.is_set():return
                try:self.sync(account['employee_id'],account['id'])
                except Exception:pass  # Public failure state is recorded by sync, never credentials.
            with ThreadPoolExecutor(max_workers=3,thread_name_prefix='imap') as pool:list(pool.map(sync_account,accounts))

    def close(self):
        self.stopping.set()
        if self.thread:self.thread.join(timeout=15)
