"""NexSearch 1.6: company workspace, INN lookup, delegated tasks and private mail."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import quote
from uuid import UUID, uuid4
import re

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import Field, field_validator
from .api15 import create_app as base_app
from .api13 import Input
from .api14 import Task
from .checko import VerificationUnavailable
from .database import NotFound, Conflict
from .mailbox import Mailbox, ACCOUNT_FIELDS, MESSAGE_FIELDS, MailError
from .notifications import notify, task_reminders, search_notifications

def email_address(value):
    value=value.strip().lower()
    if len(value)>254 or not re.fullmatch(r'[^\s@<>(),;:\\"]+@[^\s@<>(),;:\\"]+\.[^\s@<>(),;:\\"]+',value):
        raise ValueError('Проверьте адрес электронной почты')
    return value

class INNLookup(Input):
    inn:str=Field(pattern=r'^[0-9]{10}$')
    @field_validator('inn')
    @classmethod
    def valid(cls,value):
        from collector import valid_inn
        if not valid_inn(value):raise ValueError('Проверьте контрольные цифры ИНН организации')
        return value

class AssignedTask(Task):
    employee_id:UUID|None=None

class MailAccount(Input):
    email:str=Field(max_length=254)
    login:str=Field(min_length=1,max_length=254)
    password:str=Field(min_length=1,max_length=512)
    label:str=Field(default='',max_length=100)
    imap_host:str=Field(default='imap.yandex.ru',max_length=253)
    imap_port:Literal[993]=993
    smtp_host:str=Field(default='smtp.yandex.ru',max_length=253)
    smtp_port:Literal[465]=465
    @field_validator('email')
    @classmethod
    def address(cls,value):return email_address(value)
    @field_validator('imap_host','smtp_host')
    @classmethod
    def hostname(cls,value):
        value=value.strip().lower()
        if value and not re.fullmatch(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',value):raise ValueError('Укажите имя почтового сервера без https и пути')
        return value
    @field_validator('login','password')
    @classmethod
    def no_controls(cls,value):
        if any(ord(c)<32 for c in value):raise ValueError('Недопустимые символы в учётных данных')
        return value

class MailSync(Input):
    folder:str=Field(default='INBOX',min_length=1,max_length=512)
    before_uid:int|None=Field(default=None,ge=1)
class Seen(Input):seen:bool=True
class MailSettings(Input):
    label:str=Field(default='',max_length=100)
    login:str=Field(min_length=1,max_length=254)
    password:str|None=Field(default=None,max_length=512)
    imap_host:str=Field(min_length=1,max_length=253)
    smtp_host:str=Field(default='',max_length=253)
    read_mode:Literal['auto','text','html']='auto'
    signature:str=Field(default='',max_length=10000)
    notifications:bool=True
    sync_folders:list[str]=Field(default_factory=lambda:['INBOX'],min_length=1,max_length=100)
    @field_validator('imap_host','smtp_host')
    @classmethod
    def hosts(cls,value):return MailAccount.hostname(value)
    @field_validator('login','password')
    @classmethod
    def credentials(cls,value):return MailAccount.no_controls(value) if value is not None else None
class MailFolder(Input):
    name:str=Field(min_length=1,max_length=100)
    parent:str=Field(default='',max_length=512)
class MailMarkMany(Input):
    ids:list[UUID]=Field(min_length=1,max_length=500)
    seen:bool=True
class MailMove(Input):folder:str=Field(min_length=1,max_length=512)
class SendMail(Input):
    account_id:UUID
    to:list[str]=Field(min_length=1,max_length=30)
    cc:list[str]=Field(default_factory=list,max_length=30)
    subject:str=Field(min_length=1,max_length=500)
    text:str=Field(min_length=1,max_length=200000)
    reply_to:UUID|None=None
    @field_validator('to','cc')
    @classmethod
    def addresses(cls,value):return list(dict.fromkeys(email_address(v) for v in value))
    @field_validator('subject')
    @classmethod
    def subject_header(cls,value):
        if any(ord(c)<32 for c in value):raise ValueError('Недопустимая тема письма')
        return value

def create_app(*args,**kwargs):
    app=base_app(*args,**kwargs);db=app.state.db;auth=app.state.auth
    mail=app.state.mailbox=Mailbox(db)
    @app.exception_handler(RequestValidationError)
    async def validation_error(_,error):
        # Pydantic's default response echoes the invalid input, which may be a
        # mail app password. Return locations and messages, never field values.
        return JSONResponse({'error':'Проверьте заполненные поля','fields':[{'field':'.'.join(map(str,e['loc'])),'message':e['msg']} for e in error.errors()]},422)
    def employee(request:Request):
        token=request.headers.get('authorization','')
        try:
            request.state.user=auth.authenticate(token[7:] if token.startswith('Bearer ') else '',request.client.host)
            return request.state.user
        except PermissionError as error:raise HTTPException(401,str(error))

    old_lifespan=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with old_lifespan(application):
            if kwargs.get('start_worker',True):mail.start()
            try:yield
            finally:mail.close()
    app.router.lifespan_context=lifespan

    @app.post('/api/search/inn')
    def find_inn(body:INNLookup,user=Depends(employee)):
        # Check ownership before disclosing any data, including a matching cached card.
        with db.transaction(admin=True) as c:
            assigned=c.execute('SELECT l.employee_id FROM employee_leads l JOIN companies co ON co.id=l.company_id WHERE co.inn=%s',(body.inn,)).fetchone()
        if assigned and str(assigned['employee_id'])!=str(user['id']):raise Conflict('Компания уже закреплена за другим сотрудником. Дубликат не создан.')
        try:company=app.state.checker.verify(body.inn)
        except VerificationUnavailable as error:raise HTTPException(503,str(error))
        if not company.get('verification',{}).get('active'):raise HTTPException(422,'Организация не действует. В результаты поиска она не добавлена.')
        company={**company,'source':'Чекко / ФНС','source_url':company['verification']['source_url'],'notes':[],'contact_sources':[]}
        ident,_,added=db.ingest(company,user['id'])
        found=next((r for r in db.leads(user['id']) if str(r['id'])==str(ident)),None)
        if not found:raise Conflict('Компания уже закреплена за другим сотрудником. Дубликат не создан.')
        db.audit(user['id'],'search.inn',ident,{'inn':body.inn,'added':added})
        return {'company':found,'added':added}

    # Replace only the old task mutation routes; read isolation stays in PostgreSQL.
    app.router.routes[:]=[r for r in app.router.routes if not (getattr(r,'path','') in ('/api/tasks','/api/tasks/{ident}') and bool(getattr(r,'methods',set())&{'POST','PUT'}))]

    def can_assign(conn,user,target):
        row=conn.execute('SELECT nexsearch_can_assign(%s) AS allowed',(target,)).fetchone()
        if not row['allowed']:raise HTTPException(403,'Вы можете ставить задачи только себе или сотрудникам в пределах своей должности')
        person=conn.execute('SELECT id,first_name,last_name FROM employees WHERE id=%s AND active AND approved',(target,)).fetchone()
        if not person:raise NotFound('Исполнитель недоступен')
        return person

    @app.get('/api/tasks/assignees')
    def assignees(user=Depends(employee)):
        with db.transaction(user['id']) as c:
            people=c.execute('''SELECT id,first_name,last_name,role FROM employees
              WHERE active AND approved AND nexsearch_can_assign(id) ORDER BY last_name,first_name''').fetchall()
        return {'employees':people}

    @app.get('/api/tasks/companies')
    def task_companies(employee_id:UUID|None=None,user=Depends(employee)):
        target=employee_id or user['id']
        with db.transaction(user['id']) as c:
            can_assign(c,user,target)
            items=c.execute('SELECT co.id,co.name FROM employee_leads l JOIN companies co ON co.id=l.company_id WHERE l.employee_id=%s ORDER BY co.name',(target,)).fetchall()
        return {'companies':items}

    @app.get('/api/tasks/{ident}')
    def task(ident:UUID,user=Depends(employee)):
        with db.transaction(user['id']) as c:
            row=c.execute('SELECT t.*,co.name AS company_name FROM followup_tasks t LEFT JOIN companies co ON co.id=t.company_id WHERE t.id=%s',(ident,)).fetchone()
        if not row:raise NotFound('Задача недоступна')
        return row

    def save_task(body,user,ident=None):
        deliveries=[]
        with db.transaction(user['id']) as c:
            old=c.execute('SELECT * FROM followup_tasks WHERE id=%s FOR UPDATE',(ident,)).fetchone() if ident else None
            if ident and not old:raise NotFound('Задача недоступна')
            target=body.employee_id or (old['employee_id'] if old else user['id'])
            if old and str(target)!=str(old['employee_id']):raise ValueError('Для другого исполнителя создайте отдельную задачу')
            can_assign(c,user,target)
            if body.company_id and not c.execute('SELECT 1 FROM employee_leads WHERE company_id=%s AND employee_id=%s',(body.company_id,target)).fetchone():
                raise NotFound('Компания не принадлежит выбранному исполнителю')
            if body.order_id and not c.execute('SELECT 1 FROM shipment_orders WHERE id=%s AND employee_id=%s',(body.order_id,target)).fetchone():raise NotFound('Заявка недоступна исполнителю')
            if old:
                if old['revision']!=body.revision:raise Conflict('Задача уже изменена. Откройте её заново.')
                row=c.execute('''UPDATE followup_tasks SET company_id=%s,order_id=%s,title=%s,description=%s,due_at=%s,status=%s,priority=%s,
                  revision=revision+1,completed_at=CASE WHEN %s='done' THEN COALESCE(completed_at,now()) ELSE NULL END
                  WHERE id=%s RETURNING *''',(body.company_id,body.order_id,body.title,body.description,body.due_at,body.status,body.priority,body.status,ident)).fetchone()
            else:
                row=c.execute('''INSERT INTO followup_tasks(id,employee_id,assigned_by,company_id,order_id,title,description,due_at,status,priority)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',(uuid4(),target,user['id'],body.company_id,body.order_id,body.title,body.description,body.due_at,body.status,body.priority)).fetchone()
            if str(target)!=str(user['id']):
                who=user['first_name']+' '+user['last_name']
                deliveries.append((target,'task.updated' if old else 'task.assigned',row['title'],who+': '+body.description,row['id'],f"task:{row['id']}:revision:{row['revision']}"))
            if old and old['assigned_by'] and str(old['assigned_by']) not in (str(target),str(user['id'])):
                deliveries.append((old['assigned_by'],'task.updated',row['title'],'Исполнитель обновил задачу: '+row['status'],row['id'],f"task:{row['id']}:revision:{row['revision']}"))
        # Recipients were derived from the authorized task, never from an arbitrary
        # notification request. Personal mail/alert RLS remains owner-only.
        if deliveries:
            with db.transaction(admin=True) as c:
                for delivery in deliveries:notify(c,*delivery)
        db.audit(user['id'],'task.update' if ident else 'task.create',row['id'],body.model_dump(mode='json'))
        db.events.publish(target,'task.changed')
        return row

    @app.post('/api/tasks')
    def add_task(body:AssignedTask,user=Depends(employee)):return save_task(body,user)
    @app.put('/api/tasks/{ident}')
    def edit_task(ident:UUID,body:AssignedTask,user=Depends(employee)):return save_task(body,user,ident)

    @app.get('/api/notifications')
    def notifications(user=Depends(employee)):
        task_reminders(db,user['id'])
        search_notifications(db,user['id'])
        with db.transaction(user['id']) as c:
            items=c.execute('SELECT * FROM notifications WHERE employee_id=%s ORDER BY created_at DESC LIMIT 100',(user['id'],)).fetchall()
            count=c.execute('SELECT count(*) AS n FROM notifications WHERE employee_id=%s AND read_at IS NULL',(user['id'],)).fetchone()['n']
        return {'notifications':items,'unread':count,'server_time':datetime.now(timezone.utc)}
    @app.post('/api/notifications/read-all')
    def read_all(user=Depends(employee)):
        with db.transaction(user['id']) as c:c.execute('UPDATE notifications SET read_at=now() WHERE employee_id=%s AND read_at IS NULL',(user['id'],))
        return {'ok':True}
    @app.post('/api/notifications/{ident}/read')
    def read_notification(ident:UUID,user=Depends(employee)):
        with db.transaction(user['id']) as c:
            row=c.execute('UPDATE notifications SET read_at=COALESCE(read_at,now()) WHERE id=%s AND employee_id=%s RETURNING id',(ident,user['id'])).fetchone()
        if not row:raise NotFound('Уведомление недоступно')
        return {'ok':True}

    @app.get('/api/mail/accounts')
    def mail_accounts(user=Depends(employee)):
        with db.transaction(user['id']) as c:
            items=c.execute('SELECT '+ACCOUNT_FIELDS+' FROM mail_accounts WHERE employee_id=%s ORDER BY created_at',(user['id'],)).fetchall()
            counts=c.execute('SELECT account_id,folder,count(*) AS total,count(*) FILTER (WHERE NOT seen) AS unread FROM mail_messages WHERE employee_id=%s GROUP BY account_id,folder',(user['id'],)).fetchall()
        for item in items:item['folder_counts']={r['folder']:{'total':r['total'],'unread':r['unread']} for r in counts if r['account_id']==item['id']}
        return {'accounts':items}
    @app.patch('/api/mail/accounts/{ident}/settings')
    def mail_settings(ident:UUID,body:MailSettings,user=Depends(employee)):return mail.settings(user['id'],ident,body)
    @app.post('/api/mail/accounts/{ident}/folders')
    def mail_folder(ident:UUID,body:MailFolder,user=Depends(employee)):return mail.create_folder(user['id'],ident,body.name,body.parent)
    @app.post('/api/mail/accounts/{ident}/mark')
    def mail_mark_many(ident:UUID,body:MailMarkMany,user=Depends(employee)):return mail.mark_many(user['id'],ident,body.ids,body.seen)
    @app.post('/api/mail/messages/{ident}/move')
    def move_message(ident:UUID,body:MailMove,user=Depends(employee)):return mail.move(user['id'],ident,body.folder)
    @app.post('/api/mail/accounts')
    def connect_mail(body:MailAccount,user=Depends(employee)):
        if not body.imap_host:raise ValueError('Укажите IMAP сервер')
        with db.transaction(user['id']) as c:
            if c.execute('SELECT count(*) AS n FROM mail_accounts WHERE employee_id=%s',(user['id'],)).fetchone()['n']>=5:raise ValueError('Можно подключить до пяти ящиков')
        ident=mail.connect(user['id'],body)
        return {'id':ident,'connected':True}
    @app.delete('/api/mail/accounts/{ident}')
    def disconnect_mail(ident:UUID,user=Depends(employee)):
        with db.transaction(user['id']) as c:
            row=c.execute('DELETE FROM mail_accounts WHERE id=%s AND employee_id=%s RETURNING id',(ident,user['id'])).fetchone()
            if not row:raise NotFound('Почтовый аккаунт недоступен')
            c.execute("UPDATE notifications SET read_at=COALESCE(read_at,now()) WHERE employee_id=%s AND kind='mail.new' AND entity_id NOT IN (SELECT id::text FROM mail_messages WHERE employee_id=%s)",(user['id'],user['id']))
        db.audit(user['id'],'mail.disconnect',ident);return {'ok':True}
    @app.post('/api/mail/accounts/{ident}/sync')
    def sync_mail(ident:UUID,body:MailSync,user=Depends(employee)):return mail.sync(user['id'],ident,body.folder,body.before_uid)

    def company_addresses(c,user,ident):
        row=c.execute('SELECT co.payload FROM companies co JOIN employee_leads l ON l.company_id=co.id WHERE co.id=%s',(ident,)).fetchone()
        if not row:raise NotFound('Компания недоступна')
        payload=row['payload'];override=payload.get('contact_overrides') or {}
        addresses=list(payload.get('emails') or [])+list(override.get('emails') or [])
        for contact in c.execute('SELECT email FROM company_contacts WHERE company_id=%s',(ident,)):
            addresses+=re.findall(r'[^\s,;<>]+@[^\s,;<>]+',contact['email'])
        return sorted(set(a.strip().lower() for a in addresses if a and '@' in a))

    @app.get('/api/mail/messages')
    def messages(account_id:UUID|None=None,folder:str='INBOX',company_id:UUID|None=None,q:str='',before:datetime|None=None,before_uid:int|None=None,unread:bool=False,limit:int=100,user=Depends(employee)):
        where='employee_id=%s';params=[user['id']]
        with db.transaction(user['id']) as c:
            if company_id:
                addresses=company_addresses(c,user,company_id)
                where+=' AND addresses && %s::text[]';params.append(addresses)
            else:
                if account_id:where+=' AND account_id=%s';params.append(account_id)
                where+=' AND folder=%s';params.append(folder)
            if q:
                where+=' AND (subject ILIKE %s OR sender ILIKE %s OR body_text ILIKE %s)';params += ['%'+q[:200]+'%']*3
            if unread:where+=' AND NOT seen'
            if before:where+=' AND COALESCE(sent_at,cached_at)<%s';params.append(before)
            if before_uid is not None and account_id and not company_id:where+=' AND uid<%s';params.append(before_uid)
            ordering='uid DESC' if account_id and not company_id else 'COALESCE(sent_at,cached_at) DESC,id'
            items=c.execute('SELECT '+MESSAGE_FIELDS+' FROM mail_messages WHERE '+where+' ORDER BY '+ordering+' LIMIT %s',params+[max(1,min(500,limit))]).fetchall()
        return {'messages':items}
    @app.get('/api/mail/messages/{ident}')
    def read_message(ident:UUID,user=Depends(employee)):return mail.read(user['id'],ident)
    @app.patch('/api/mail/messages/{ident}')
    def mark_message(ident:UUID,body:Seen,user=Depends(employee)):mail.mark(user['id'],ident,body.seen);return {'ok':True}
    @app.get('/api/mail/messages/{ident}/attachments/{index}')
    def attachment(ident:UUID,index:int,user=Depends(employee)):
        name,data=mail.attachment(user['id'],ident,index)
        return Response(data,media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(name,safe=''),'Content-Security-Policy':"sandbox; default-src 'none'"})
    @app.post('/api/mail/send')
    def send_message(body:SendMail,user=Depends(employee)):return mail.send(user['id'],body)
    @app.get('/api/remote/status')
    def remote_status(user=Depends(employee)):
        remote=getattr(app.state,'remote_access',None)
        if not remote:return {'phase':'unavailable','message':'Удалённый доступ недоступен в этом тестовом запуске'}
        result=remote.status()
        if user['role']!='director':result.pop('auth_url',None);result.pop('config',None)
        return result
    @app.post('/api/remote/config')
    def remote_config(body:dict,user=Depends(employee)):
        if user['role']!='director':raise HTTPException(403,'Настраивать удалённый доступ хоста может только директор')
        remote=getattr(app.state,'remote_access',None)
        if not remote:raise HTTPException(503,'Модуль удалённого доступа хоста недоступен')
        try:result=remote.configure(body)
        except ValueError as error:raise HTTPException(422,str(error)) from None
        db.audit(user['id'],'remote.configure','host',{'enabled':result['config']['enabled'],'provider':result['config']['provider']});return result
    @app.post('/api/remote/login')
    def remote_login(user=Depends(employee)):
        if user['role']!='director':raise HTTPException(403,'Подключить хост к VPN может только директор')
        remote=getattr(app.state,'remote_access',None)
        if not remote:raise HTTPException(503,'Модуль удалённого доступа хоста недоступен')
        return remote.login()
    return app
