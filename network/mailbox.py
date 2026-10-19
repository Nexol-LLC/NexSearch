"""Personal IMAP/SMTP mail; sealed passwords, reconnectable reads, sandboxed HTML."""
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
import time
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
from .mail_html import sanitize_html,raster_uri

MAX_MESSAGE=15*1024*1024
ACCOUNT_FIELDS='id,email,login,label,imap_host,imap_port,smtp_host,smtp_port,enabled,folders,settings,last_sync_at,last_error'
MESSAGE_FIELDS='id,account_id,folder,uid,subject,sender,recipients,addresses,sent_at,seen,size_bytes,message_id,in_reply_to,reference_ids'

class MailError(ValueError):pass
class MailConnectionError(MailError):pass
class MailAuthenticationError(MailError):pass

def message_ids(value):return list(dict.fromkeys(re.findall(r'<[^<>\s]{1,998}>',str(value or ''))))[-50:]

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
    last=None;deadline=time.monotonic()+timeout
    for family,kind,proto,_,address in sorted(addresses,key=lambda a:a[0]!=socket.AF_INET):
        remaining=deadline-time.monotonic()
        if remaining<=0:break
        sock=socket.socket(family,kind,proto);sock.settimeout(min(4,remaining))
        try:sock.connect(address);sock.settimeout(timeout);return sock
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
        match=re.match(rb'\(([^)]*)\)\s+("(?:[^"\\]|\\.)*"|NIL)\s+(.+)$',line)
        if not match:continue
        flags=match[1].decode('ascii','ignore').lower()
        delimiter=match[2].decode('ascii').strip('"') if match[2]!=b'NIL' else None
        raw=match[3].decode('ascii','replace')
        if raw.startswith('"') and raw.endswith('"'):raw=re.sub(r'\\(.)',r'\1',raw[1:-1])
        if len(raw)>512 or any(ord(c)<32 for c in raw):continue
        display=folder_name(raw)
        sent='\\sent' in flags or display.casefold() in ('sent','sent items','sent messages','отправленные')
        result.append({'id':raw,'name':display,'sent':sent,'delimiter':delimiter,'selectable':'\\noselect' not in flags,'quiet':sent or any(flag in flags for flag in ('\\drafts','\\trash','\\junk'))})
    if not any(f['id'].upper()=='INBOX' for f in result):result.insert(0,{'id':'INBOX','name':'Входящие','sent':False})
    return sorted(result,key=lambda f:(f['id'].upper()!='INBOX',f['name'].casefold()))[:100]

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

def rich_content(message):
    text,attachments=content(message);html=[];inline={}
    for part in message.walk():
        if part.is_multipart():continue
        cid=str(part.get('Content-ID','')).strip('<>')
        if cid and part.get_content_type().startswith('image/'):
            image=raster_uri(part.get_payload(decode=True) or b'',part.get_content_type())
            if image:inline[cid]=image
        if part.get('Content-Location') and part.get_content_type().startswith('image/'):
            image=raster_uri(part.get_payload(decode=True) or b'',part.get_content_type())
            if image:inline[str(part['Content-Location'])]=image
        if part.get_content_type()=='text/html' and part.get_content_disposition()!='attachment' and not part.get_filename():
            try:html.append(str(part.get_content()))
            except (LookupError,UnicodeError):html.append((part.get_payload(decode=True) or b'').decode('utf-8','replace'))
    return text,sanitize_html('\n'.join(html),inline) if html else '',attachments

def encode_folder(value):
    result=[];run=[]
    def flush():
        if run:result.append('&'+base64.b64encode(''.join(run).encode('utf-16-be')).decode().rstrip('=').replace('/',',')+'-');run.clear()
    for char in value:
        if 32<=ord(char)<=126:flush();result.append('&-' if char=='&' else char)
        else:run.append(char)
    flush();return ''.join(result)

def quoted_folder(value):
    if not value or len(value)>512 or any(ord(c)<32 or ord(c)>126 for c in value):raise MailError('Недопустимая папка')
    return '"'+value.replace('\\','\\\\').replace('"','\\"')+'"'

def headers(raw,metadata):
    message=BytesParser(policy=policy.default).parsebytes(raw,headersonly=True)
    senders=getaddresses(message.get_all('from',[]));recipients=getaddresses(message.get_all('to',[])+message.get_all('cc',[]))
    addresses=list(dict.fromkeys(address.lower() for _,address in senders+recipients if address))
    try:date=parsedate_to_datetime(str(message.get('date','')));date=date if date.tzinfo else date.replace(tzinfo=timezone.utc)
    except (TypeError,ValueError,OverflowError):date=None
    return {'subject':str(message.get('subject','Без темы'))[:1000],'sender':str(message.get('from',''))[:1000],
      'recipients':[a for _,a in recipients],'addresses':addresses,'sent_at':date,'message_id':next(iter(message_ids(message.get('message-id',''))),'') ,
      'in_reply_to':next(iter(message_ids(message.get('in-reply-to',''))),'') , 'reference_ids':message_ids(message.get('references','')),
      'seen':b'\\Seen' in metadata,'size_bytes':int((re.search(rb'RFC822.SIZE (\d+)',metadata) or [b'',b'0'])[1])}

class Mailbox:
    def __init__(self,db):
        self.db=db;self.stopping=threading.Event();self.thread=None
        self.locks=[threading.Lock() for _ in range(64)];self.slots=threading.BoundedSemaphore(3)
        self.pool=None;self.inflight={};self.next_due={}
        self.connection_locks=[threading.RLock() for _ in range(64)]
        from .mail_images import ImageCache
        self.images=ImageCache()

    @contextmanager
    def connection(self,account):
        client=None;phase='connect';lock=self.connection_locks[hash(str(account['id']))%len(self.connection_locks)]
        if not lock.acquire(timeout=4):raise Conflict('Почта обновляется. Повторите действие через несколько секунд.')
        acquired=False
        try:
            acquired=self.slots.acquire(timeout=4)
            if not acquired:raise Conflict('Почтовые подключения заняты. Повторите через несколько секунд.')
            client=SecureIMAP(account['imap_host'],account['imap_port'],ssl_context=ssl.create_default_context(),timeout=12)
            phase='login'
            client.login(account['login'],unseal(account['secret'],account['employee_id'],account['id']))
            phase='operation'
            yield client
            phase='done'
        except imaplib.IMAP4.abort:raise MailConnectionError('Соединение с почтовым сервером прервано. Переподключаемся автоматически; ящик отключать не нужно.') from None
        except imaplib.IMAP4.error as error:
            if phase=='login':
                if re.search(r'unavailable|too many|rate.?limit|temporar|try again|\[limit\]',str(error),re.I):raise MailConnectionError('Почтовый сервер временно ограничил подключения. Повторим вход автоматически.') from None
                raise MailAuthenticationError('Сервер отклонил вход. Проверьте пароль приложения в настройках ящика.') from None
            raise MailError('Почтовый сервер не выполнил операцию. Повторите позже.') from None
        except (OSError,ssl.SSLError,EOFError):raise MailConnectionError('Почтовый сервер временно недоступен. Переподключимся автоматически.') from None
        except UnicodeError:raise MailError('Почтовый сервер прислал данные в неподдерживаемой кодировке') from None
        except InvalidTag:raise MailError('Защищённый пароль недоступен. Подключите почтовый ящик заново.') from None
        finally:
            if client:
                try:
                    if phase=='done':
                        if getattr(client,'sock',None):client.sock.settimeout(2)
                        client.logout()
                except Exception:pass
                try:client.shutdown()
                except Exception:pass
            if acquired:self.slots.release()
            lock.release()

    def reconnect_read(self,operation):
        for attempt in range(2):
            try:return operation()
            except MailConnectionError:
                if attempt:raise
                if self.stopping.wait(.25):raise

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
            return self.reconnect_read(lambda:self._sync(owner,ident,folder,before_uid))
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
                status,data=client.uid('fetch',','.join(map(str,batch)),'(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO CC DATE MESSAGE-ID IN-REPLY-TO REFERENCES)])')
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
                row=c.execute('''INSERT INTO mail_messages(id,employee_id,account_id,folder,uidvalidity,uid,subject,sender,recipients,addresses,sent_at,seen,size_bytes,message_id,in_reply_to,reference_ids)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                  ON CONFLICT(account_id,folder,uidvalidity,uid) DO UPDATE SET seen=excluded.seen,in_reply_to=excluded.in_reply_to,reference_ids=excluded.reference_ids
                  RETURNING id''',(uuid4(),owner,ident,folder,validity,uid,item['subject'],item['sender'],Jsonb(item['recipients']),item['addresses'],item['sent_at'],item['seen'],item['size_bytes'],item['message_id'],item['in_reply_to'],item['reference_ids'])).fetchone()
                folder_info=next((f for f in folders if f['id']==folder),{})
                if not initial and not folder_info.get('quiet',folder_info.get('sent',False)) and (account.get('settings') or {}).get('notifications',True) and uid>highest and not item['seen']:
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
        return self.reconnect_read(lambda:self._raw_message(owner,row))

    def _raw_message(self,owner,row):
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
        warning=''
        if row['body_text'] is None or row['content_version']<2:
            try:
                raw=self.raw_message(owner,row);body,html,attachments=rich_content(raw)
                parent=next(iter(message_ids(raw.get('in-reply-to',''))),'');refs=message_ids(raw.get('references',''));message_id=next(iter(message_ids(raw.get('message-id',''))),row['message_id'])
                with self.db.transaction(owner) as c:c.execute('UPDATE mail_messages SET body_text=%s,body_html=%s,content_version=2,attachments=%s,in_reply_to=%s,reference_ids=%s,message_id=%s WHERE id=%s AND employee_id=%s',(body,html,Jsonb(attachments),parent,refs,message_id,ident,owner))
                row.update(body_text=body,body_html=html,attachments=attachments,content_version=2,in_reply_to=parent,reference_ids=refs,message_id=message_id)
            except (MailError,Conflict):
                if row['body_text'] is None:raise
                warning='Почтовый сервер недоступен. Показана сохранённая копия письма.'
        if not row['seen']:
            try:self.mark(owner,ident,True);row['seen']=True
            except (MailError,Conflict):warning=warning or 'Письмо открыто, но отметку прочтения не удалось синхронизировать. Повторите позже.'
        account=self.account(owner,row['account_id'])
        from .mail_images import image_urls
        return {**{k:v for k,v in row.items() if k not in ('employee_id','uid','uidvalidity')},'remote_image_count':len(image_urls(row.get('body_html') or '')),'warning':warning,'read_mode':(account.get('settings') or {}).get('read_mode','auto')}

    def image(self,owner,ident,index):
        from .mail_images import image_urls,ImageUnavailable
        row=self.message(owner,ident);urls=image_urls(row.get('body_html') or '')
        if index<0 or index>=len(urls):raise NotFound('Изображение не найдено')
        try:return self.images.get(owner,urls[index],public_socket)
        except ImageUnavailable as exc:raise MailError(str(exc)) from None

    def mark(self,owner,ident,seen):
        row=self.message(owner,ident)
        return self.mark_many(owner,row['account_id'],[ident],seen)

    def attachment(self,owner,ident,index):
        row=self.message(owner,ident);message=self.raw_message(owner,row)
        parts=list(message.walk())
        if index<0 or index>=len(parts):raise NotFound('Вложение не найдено')
        part=parts[index]
        if not part.get_filename() and part.get_content_disposition()!='attachment':raise NotFound('Вложение не найдено')
        return str(part.get_filename() or 'attachment'),part.get_payload(decode=True) or b''

    def settings(self,owner,ident,body):
        lock=self.locks[hash(str(ident))%len(self.locks)]
        if not lock.acquire(blocking=False):raise Conflict('Дождитесь завершения синхронизации почты')
        try:return self._settings(owner,ident,body)
        finally:lock.release()

    def _settings(self,owner,ident,body):
        old=self.account(owner,ident);changed=any(getattr(body,k)!=old[k] for k in ('login','imap_host','smtp_host')) or bool(body.password)
        updated={**old,'login':body.login,'imap_host':body.imap_host,'smtp_host':body.smtp_host}
        if body.password:updated['secret']=seal(body.password,owner,ident)
        folders=old['folders']
        if changed:
            with self.connection(updated) as client:
                status,lines=client.list()
                if status!='OK':raise MailError('Не удалось проверить папки почты')
                folders=folders_from_list(lines)
        if not set(body.sync_folders)<={f['id'] for f in folders if f.get('selectable',True)}:raise MailError('Обновите список папок и выберите доступные папки')
        settings={'read_mode':body.read_mode,'signature':body.signature,'notifications':body.notifications,'sync_folders':list(dict.fromkeys(body.sync_folders))}
        with self.db.transaction(owner) as c:
            c.execute('''UPDATE mail_accounts SET label=%s,login=%s,imap_host=%s,smtp_host=%s,secret=%s,settings=%s,folders=%s,last_error=''
              WHERE id=%s AND employee_id=%s''',(body.label,body.login,body.imap_host,body.smtp_host,updated['secret'],Jsonb(settings),Jsonb(folders),ident,owner))
            # A different IMAP server may reuse UIDVALIDITY and UID numbers.
            if updated['imap_host']!=old['imap_host'] or updated['login']!=old['login']:
                c.execute('DELETE FROM mail_messages WHERE account_id=%s AND employee_id=%s',(ident,owner))
                c.execute("UPDATE mail_accounts SET sync_state='{}',last_sync_at=NULL WHERE id=%s AND employee_id=%s",(ident,owner))
        self.db.audit(owner,'mail.settings',ident,{'credentials_changed':changed,'read_mode':body.read_mode})
        self.db.events.publish(owner,'mail.settings');return {'saved':True}

    def create_folder(self,owner,ident,name,parent=''):
        account=self.account(owner,ident);name=name.strip()
        if not name or len(name)>100 or any(ord(c)<32 for c in name):raise MailError('Укажите название папки до 100 символов')
        lock=self.locks[hash(str(ident))%len(self.locks)]
        if not lock.acquire(blocking=False):raise Conflict('Дождитесь завершения синхронизации почты')
        try:
            with self.connection(account) as client:
                status,lines=client.list()
                if status!='OK':raise MailError('Не удалось получить папки')
                folders=folders_from_list(lines);parent_info=next((f for f in folders if f['id']==parent),None)
                if parent and not parent_info:raise MailError('Родительская папка недоступна')
                delimiter=(parent_info or next(iter(folders),{})).get('delimiter','/')
                if parent and not delimiter:raise MailError('Этот сервер не поддерживает вложенные папки')
                if delimiter and delimiter in name:raise MailError('В названии нельзя использовать разделитель '+delimiter)
                raw=(parent+delimiter if parent else '')+encode_folder(name)
                if any(f['id'].casefold()==raw.casefold() for f in folders):raise Conflict('Такая папка уже есть')
                status,_=client.create(quoted_folder(raw))
                if status!='OK':raise MailError('Почтовый сервер не создал папку')
                try:client.subscribe(quoted_folder(raw))
                except imaplib.IMAP4.error:pass
                status,lines=client.list()
                if status!='OK':raise MailError('Папка создана; обновите список папок')
                folders=folders_from_list(lines)
            with self.db.transaction(owner) as c:
                c.execute('UPDATE mail_accounts SET folders=%s WHERE id=%s AND employee_id=%s',(Jsonb(folders),ident,owner))
            self.db.audit(owner,'mail.folder_create',ident,{'name':name});self.db.events.publish(owner,'mail.folders')
            return {'id':raw,'folders':folders}
        finally:lock.release()

    def mark_many(self,owner,ident,ids,seen):
        self.account(owner,ident)
        lock=self.locks[hash(str(ident))%len(self.locks)]
        if not lock.acquire(blocking=False):raise Conflict('Дождитесь завершения синхронизации почты')
        try:return self._mark_many(owner,ident,ids,seen)
        finally:lock.release()

    def _mark_many(self,owner,ident,ids,seen):
        self.account(owner,ident)
        with self.db.transaction(owner) as c:rows=c.execute('SELECT * FROM mail_messages WHERE id=ANY(%s) AND account_id=%s AND employee_id=%s',(list(set(ids)),ident,owner)).fetchall()
        if len(rows)!=len(set(ids)):raise NotFound('Письма недоступны')
        account=self.account(owner,ident)
        with self.connection(account) as client:
            for folder in {r['folder'] for r in rows}:
                validity=select_folder(client,folder,False);selected=[r for r in rows if r['folder']==folder]
                if any(r['uidvalidity']!=validity for r in selected):raise Conflict('Синхронизируйте папку заново')
                status,_=client.uid('store',','.join(str(r['uid']) for r in selected),'+FLAGS.SILENT' if seen else '-FLAGS.SILENT','(\\Seen)')
                if status!='OK':raise MailError('Не удалось изменить отметки прочтения')
        with self.db.transaction(owner) as c:
            c.execute('UPDATE mail_messages SET seen=%s WHERE id=ANY(%s) AND employee_id=%s',(seen,ids,owner))
            if seen:c.execute("UPDATE notifications SET read_at=COALESCE(read_at,now()) WHERE employee_id=%s AND kind='mail.new' AND entity_id=ANY(%s)",(owner,[str(i) for i in ids]))
        self.db.events.publish(owner,'mail.flags');return {'updated':len(rows)}

    def move(self,owner,ident,folder):
        row=self.message(owner,ident)
        lock=self.locks[hash(str(row['account_id']))%len(self.locks)]
        if not lock.acquire(blocking=False):raise Conflict('Дождитесь завершения синхронизации почты')
        try:return self._move(owner,ident,folder)
        finally:lock.release()

    def _move(self,owner,ident,folder):
        row=self.message(owner,ident);account=self.account(owner,row['account_id'])
        if folder not in [f['id'] for f in account['folders']]:raise MailError('Папка недоступна')
        if folder==row['folder']:return {'moved':False}
        with self.connection(account) as client:
            if b'MOVE' not in client.capabilities:raise MailError('Этот сервер не поддерживает безопасное перемещение писем')
            if select_folder(client,row['folder'],False)!=row['uidvalidity']:raise Conflict('Синхронизируйте папку заново')
            status,_=client.uid('MOVE',str(row['uid']),quoted_folder(folder))
            if status!='OK':raise MailError('Не удалось переместить письмо')
        with self.db.transaction(owner) as c:
            c.execute('DELETE FROM mail_messages WHERE id=%s AND employee_id=%s',(ident,owner))
            c.execute("UPDATE notifications SET read_at=COALESCE(read_at,now()) WHERE employee_id=%s AND entity_id=%s AND kind='mail.new'",(owner,str(ident)))
        self.db.events.publish(owner,'mail.move');return {'moved':True}

    def send(self,owner,body):
        account=self.account(owner,body.account_id)
        if not account['smtp_host']:raise MailError('Исходящая почта для этого ящика не настроена')
        message=EmailMessage();message['From']=account['email'];message['To']=', '.join(body.to)
        if body.cc:message['Cc']=', '.join(body.cc)
        message['Subject']=body.subject;message['Date']=formatdate(localtime=False);message['Message-ID']=make_msgid()
        if body.reply_to:
            previous=self.message(owner,body.reply_to)
            if previous['account_id']!=account['id']:raise MailError('Ответ нужно отправлять из ящика, в котором получено письмо')
            if previous['message_id']:
                message['In-Reply-To']=previous['message_id'];message['References']=' '.join(list(dict.fromkeys([*previous.get('reference_ids',[]),previous['message_id']]))[-50:])
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
        self.next_due[str(account['id'])]=0
        return {'sent':True,'warning':warning}

    def start(self):
        if self.thread:return
        self.thread=threading.Thread(target=self.run,name='personal-mail-sync',daemon=True);self.thread.start()

    def run(self):
        self.pool=ThreadPoolExecutor(max_workers=3,thread_name_prefix='imap')
        try:
            while not self.stopping.is_set():
                try:self.tick()
                except Exception:pass  # A transient DB error must not kill auto-sync.
                self.stopping.wait(1)
        finally:self.pool.shutdown(wait=False,cancel_futures=True)

    def tick(self,now=None):
        now=time.monotonic() if now is None else now
        self.inflight={key:f for key,f in self.inflight.items() if not f.done()}
        with self.db.transaction(admin=True) as c:
            accounts=c.execute('SELECT a.id,a.employee_id,a.settings FROM mail_accounts a JOIN employees e ON e.id=a.employee_id WHERE a.enabled AND e.active AND e.approved').fetchall()
        active={str(a['id']) for a in accounts};self.next_due={key:due for key,due in self.next_due.items() if key in active}
        for account in sorted(accounts,key=lambda a:self.next_due.get(str(a['id']),0)):
            key=str(account['id'])
            if len(self.inflight)>=3:break
            if key in self.inflight or self.next_due.get(key,0)>now:continue
            self.next_due[key]=now+60;self.inflight[key]=self.pool.submit(self.sync_account,account)

    def sync_account(self,account):
        folders=list((account.get('settings') or {}).get('sync_folders',['INBOX']))
        # Sent copies are required to show the employee's own replies in a conversation.
        current=self.account(account['employee_id'],account['id'])
        folders+= [f['id'] for f in current['folders'] if f.get('sent') and f['id'] not in folders]
        for folder in folders:
            if self.stopping.is_set():return
            try:self.sync(account['employee_id'],account['id'],folder)
            except Exception:pass

    def close(self):
        self.stopping.set()
        if self.thread:self.thread.join(timeout=15)
