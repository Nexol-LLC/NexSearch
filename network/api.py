from contextlib import asynccontextmanager
from datetime import datetime,timezone
from pathlib import Path
from uuid import UUID,uuid4
import io
import os
import re
import threading
import time
import xlsxwriter
from fastapi import FastAPI,Request,Depends,HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse,Response,FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field,ConfigDict
from psycopg.types.json import Jsonb
from psycopg.errors import UniqueViolation
from .database import Database,Conflict,NotFound
from .auth import Authentication,public_employee
from .search import SearchWorker
from source_catalog import SOURCES

class Input(BaseModel):model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
class Register(Input):
    first_name:str=Field(min_length=1,max_length=100)
    last_name:str=Field(min_length=1,max_length=100)
    password:str=Field(min_length=8,max_length=256)
    login:str|None=Field(default=None,pattern=r'^[a-zA-Z0-9._-]{3,64}$')
    device_id:str=Field(min_length=1,max_length=128)
    computer_name:str=Field(default='',max_length=128)
class Login(Input):
    login:str=Field(min_length=1,max_length=64)
    password:str=Field(min_length=1,max_length=256)
    device_id:str=Field(min_length=1,max_length=128)
    computer_name:str=Field(default='',max_length=128)
class Search(Input):target:int|None=10
class Lead(Input):
    status:str=Field(pattern=r'^(new|processing|refused|taken)$')
    called:bool
    call_note:str=Field(default='',max_length=10000)
    revision:int=Field(ge=0)
class Stage(Input):stage:str=Field(max_length=500);revision:int=Field(ge=0)
class Carrier(Input):
    name:str=Field(min_length=1,max_length=500)
    contacts:str=Field(default='',max_length=5000)
    routes:str=Field(default='',max_length=5000)
    description:str=Field(default='',max_length=10000)
    revision:int|None=None
class Plan(Input):name:str=Field(min_length=1,max_length=200);project:dict;revision:int|None=None
class Active(Input):active:bool
class Legacy(Input):companies:list[dict]=Field(max_length=5000)

def create_app(database=None,web=None,pack=None,start_worker=True):
    db=database or Database(os.environ['NEXOL_DATABASE_URL'])
    db.initialize();auth=Authentication(db);worker=SearchWorker(db)
    @asynccontextmanager
    async def lifespan(app):
        if start_worker:worker.start()
        yield
        if start_worker:worker.stop()
        if database is None:db.close()
    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.db=db;app.state.auth=auth;app.state.worker=worker
    attempts={};rate_lock=threading.Lock()
    def throttle(request,login):
        key=(request.client.host,login.lower())
        with rate_lock:
            now=time.monotonic();values=[t for t in attempts.get(key,[]) if now-t<300]
            if len(values)>=12:raise HTTPException(429,'Слишком много попыток входа. Повторите через 5 минут.')
            attempts[key]=[*values,now]
            if len(attempts)>2000:
                for k in list(attempts):
                    if not attempts[k] or now-attempts[k][-1]>300:attempts.pop(k,None)
    def token(request):
        header=request.headers.get('authorization','')
        return header[7:] if header.startswith('Bearer ') else ''
    def employee(request:Request):
        try:return auth.authenticate(token(request),request.client.host)
        except PermissionError as ex:raise HTTPException(401,str(ex))
    def admin(user=Depends(employee)):
        if not user['is_admin']:raise HTTPException(403,'Доступ только администратору хоста')
        return user
    @app.exception_handler(Conflict)
    async def conflict(_,ex):return JSONResponse({'error':str(ex)},409)
    @app.exception_handler(NotFound)
    async def missing(_,ex):return JSONResponse({'error':str(ex)},404)
    @app.exception_handler(ValueError)
    async def invalid(_,ex):return JSONResponse({'error':str(ex)},400)
    @app.exception_handler(HTTPException)
    async def http_error(_,ex):return JSONResponse({'error':ex.detail},ex.status_code)
    @app.middleware('http')
    async def limits(request,call_next):
        try:length=int(request.headers.get('content-length','0') or 0)
        except ValueError:return JSONResponse({'error':'Недопустимая длина запроса'},400)
        if length<0 or length>5*1024*1024:return JSONResponse({'error':'Слишком большой запрос'},413)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Cache-Control']='no-store'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; frame-src 'self'; frame-ancestors 'self'; connect-src 'self'; base-uri 'none'"
        return response
    @app.get('/api/host-info')
    def host_info():return dict(app='nexol-host',protocol=1,host_id=os.environ.get('NEXOL_HOST_ID','development'))
    @app.post('/api/auth/register')
    def register(body:Register,request:Request):
        throttle(request,'register')
        return auth.register(body.first_name,body.last_name,body.password,body.device_id,body.computer_name,request.client.host,body.login)
    @app.post('/api/auth/login')
    def login(body:Login,request:Request):
        throttle(request,body.login)
        try:return auth.login(body.login,body.password,body.device_id,body.computer_name,request.client.host)
        except PermissionError as ex:raise HTTPException(401,str(ex))
    @app.post('/api/auth/logout')
    def logout(request:Request,user=Depends(employee)):auth.logout(token(request));return {'ok':True}
    @app.get('/api/me')
    def me(user=Depends(employee)):return {'employee':user}
    @app.get('/api/state')
    def state(user=Depends(employee)):
        with db.transaction(user['id'],user['is_admin']) as conn:
            jobs=conn.execute('SELECT * FROM jobs WHERE employee_id=%s ORDER BY created_at DESC LIMIT 10',(user['id'],)).fetchall()
            count=conn.execute('SELECT count(*) AS n FROM companies' if user['is_admin'] else 'SELECT count(*) AS n FROM employee_leads').fetchone()['n']
            clients=conn.execute('SELECT count(*) AS n FROM employee_clients WHERE employee_id=%s AND active',(user['id'],)).fetchone()['n']
            carriers=conn.execute('SELECT count(*) AS n FROM carriers').fetchone()['n']
        return dict(employee=user,jobs=jobs,stats=dict(found=count,clients=clients,carriers=carriers),sources=SOURCES)
    @app.get('/api/leads')
    def leads(user=Depends(employee)):
        if user['is_admin']:
            with db.transaction(admin=True) as conn:
                rows=conn.execute('''SELECT c.id,c.payload,l.status AS lead_status,l.employee_id,e.login AS assigned_to,l.called,l.call_note,l.revision
                 FROM companies c LEFT JOIN employee_leads l ON l.company_id=c.id LEFT JOIN employees e ON e.id=l.employee_id ORDER BY c.created_at DESC''').fetchall()
                return dict(companies=[dict(r.pop('payload'),**r) for r in rows])
        return {'companies':db.leads(user['id'])}
    @app.patch('/api/leads/{company_id}')
    def update_lead(company_id:UUID,body:Lead,user=Depends(employee)):
        return db.update_lead(user['id'],company_id,body.status,body.called,body.call_note,body.revision)
    @app.get('/api/clients')
    def clients(stage:str|None=None,order:str='recent',user=Depends(employee)):
        if order not in ('recent','stage','name'):raise HTTPException(400,'Неизвестный порядок сортировки')
        return {'companies':db.leads(user['id'],clients=True,stage=stage,order=order)}
    @app.get('/api/clients/stages')
    def stages(user=Depends(employee)):
        with db.transaction(user['id']) as conn:return {'stages':[r['stage'] for r in conn.execute('SELECT DISTINCT stage FROM employee_clients WHERE active ORDER BY stage')]}
    @app.patch('/api/clients/{company_id}/stage')
    def change_stage(company_id:UUID,body:Stage,user=Depends(employee)):return db.update_stage(user['id'],company_id,body.stage,body.revision)
    @app.post('/api/search')
    def search(body:Search,user=Depends(employee)):
        if body.target not in (10,None):raise HTTPException(400,'Можно собрать 10 или все новые компании')
        try:
            with db.transaction(user['id']) as conn:
                row=conn.execute('INSERT INTO jobs(id,employee_id,target,message) VALUES(%s,%s,%s,%s) RETURNING *',(uuid4(),user['id'],body.target,'В очереди хоста')).fetchone()
        except UniqueViolation:raise HTTPException(409,'Ваш поиск уже выполняется')
        worker.wake.set();return row
    @app.post('/api/search/{job_id}/stop')
    def stop(job_id:UUID,user=Depends(employee)):
        with db.transaction(user['id']) as conn:
            row=conn.execute("UPDATE jobs SET stop_requested=true WHERE id=%s AND employee_id=%s AND status IN ('queued','running') RETURNING *",(job_id,user['id'])).fetchone()
            if not row:raise NotFound('Нет активного поиска')
            if row['status']=='queued':conn.execute("UPDATE jobs SET status='stopped',finished_at=now() WHERE id=%s",(job_id,))
        return {'ok':True}
    @app.get('/api/carriers')
    def carriers(user=Depends(employee)):
        with db.transaction(user['id']) as conn:return {'carriers':conn.execute('''SELECT c.*,e.first_name||' '||e.last_name AS author FROM carriers c JOIN employees e ON e.id=c.created_by ORDER BY c.updated_at DESC''').fetchall()}
    @app.post('/api/carriers')
    def add_carrier(body:Carrier,user=Depends(employee)):
        with db.transaction(user['id']) as conn:return conn.execute('''INSERT INTO carriers(id,name,contacts,routes,description,created_by,updated_by)
          VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *''',(uuid4(),body.name,body.contacts,body.routes,body.description,user['id'],user['id'])).fetchone()
    @app.patch('/api/carriers/{ident}')
    def edit_carrier(ident:UUID,body:Carrier,user=Depends(employee)):
        with db.transaction(user['id']) as conn:
            old=conn.execute('SELECT * FROM carriers WHERE id=%s FOR UPDATE',(ident,)).fetchone()
            if not old:raise NotFound('Перевозчик не найден')
            if old['revision']!=body.revision:raise Conflict('Перевозчик изменён другим сотрудником. Обновите запись.')
            return conn.execute('''UPDATE carriers SET name=%s,contacts=%s,routes=%s,description=%s,updated_by=%s,
             revision=revision+1,updated_at=now() WHERE id=%s RETURNING *''',(body.name,body.contacts,body.routes,body.description,user['id'],ident)).fetchone()
    @app.get('/api/plans')
    def plans(user=Depends(employee)):
        with db.transaction(user['id']) as conn:return {'plans':conn.execute('SELECT id,name,revision,updated_at FROM loading_plans ORDER BY updated_at DESC').fetchall()}
    @app.get('/api/plans/{ident}')
    def plan(ident:UUID,user=Depends(employee)):
        with db.transaction(user['id']) as conn:
            row=conn.execute('SELECT * FROM loading_plans WHERE id=%s',(ident,)).fetchone()
            if not row:raise NotFound('План не найден')
            return row
    @app.post('/api/plans')
    def add_plan(body:Plan,user=Depends(employee)):
        with db.transaction(user['id']) as conn:return conn.execute('INSERT INTO loading_plans(id,employee_id,name,project) VALUES(%s,%s,%s,%s) RETURNING id,name,revision',(uuid4(),user['id'],body.name,Jsonb(body.project))).fetchone()
    @app.put('/api/plans/{ident}')
    def edit_plan(ident:UUID,body:Plan,user=Depends(employee)):
        with db.transaction(user['id']) as conn:
            old=conn.execute('SELECT * FROM loading_plans WHERE id=%s FOR UPDATE',(ident,)).fetchone()
            if not old:raise NotFound('План не найден')
            if old['revision']!=body.revision:raise Conflict('План уже изменён. Откройте его заново.')
            return conn.execute('UPDATE loading_plans SET name=%s,project=%s,revision=revision+1,updated_at=now() WHERE id=%s RETURNING id,name,revision',(body.name,Jsonb(body.project),ident)).fetchone()
    @app.post('/api/import/legacy')
    def import_legacy(body:Legacy,user=Depends(employee)):
        from collector import valid_inn
        added=0;skipped=0
        for item in body.companies:
            if not isinstance(item.get('name'),str) or not item['name'].strip() or len(item['name'])>500:continue
            if item.get('inn') and not valid_inn(item['inn']):item['inn']=''
            ident,_,assigned=db.ingest(item,user['id'])
            if assigned:
                added+=1
                called=bool(item.get('called'));status='taken' if item.get('lead_status')=='taken' and called else 'new'
                db.update_lead(user['id'],ident,status,called,str(item.get('call_note',''))[:10000],0)
            else:skipped+=1
        return dict(added=added,skipped=skipped)
    @app.get('/api/admin/employees')
    def employees(user=Depends(admin)):
        with db.transaction(admin=True) as conn:
            rows=conn.execute('''SELECT e.id,e.login,e.first_name,e.last_name,e.active,e.is_admin,
               (SELECT max(d.last_seen) FROM devices d WHERE d.employee_id=e.id) AS last_seen,
               (SELECT count(*) FROM employee_leads l WHERE l.employee_id=e.id) AS found_count,
               (SELECT count(*) FROM employee_clients cl WHERE cl.employee_id=e.id AND cl.active) AS client_count
               FROM employees e ORDER BY e.created_at''').fetchall()
            devices=conn.execute('SELECT id,employee_id,computer_name,ip,last_seen FROM devices ORDER BY last_seen DESC').fetchall()
            return dict(employees=rows,devices=devices)
    @app.patch('/api/admin/employees/{ident}/active')
    def active(ident:UUID,body:Active,user=Depends(admin)):
        if ident==user['id']:raise ValueError('Нельзя отключить собственную учётную запись хоста')
        with db.transaction(admin=True) as conn:
            row=conn.execute('UPDATE employees SET active=%s WHERE id=%s RETURNING id,active',(body.active,ident)).fetchone()
            if not row:raise NotFound('Сотрудник не найден')
            if not body.active:conn.execute('DELETE FROM sessions WHERE employee_id=%s',(ident,))
            return row
    @app.get('/api/admin/employees/{ident}/clients')
    def employee_clients(ident:UUID,user=Depends(admin)):return {'companies':db.leads(ident,clients=True)}
    @app.get('/api/export/{kind}')
    def export(kind:str,user=Depends(employee)):
        if kind=='carriers':
            with db.transaction(user['id']) as conn:items=conn.execute('SELECT * FROM carriers ORDER BY name').fetchall()
            headers=['Наименование','Контакты','Маршруты','Описание'];rows=[[r['name'],r['contacts'],r['routes'],r['description']] for r in items]
        elif kind in ('leads','clients'):
            items=db.leads(user['id'],clients=kind=='clients')
            headers=['Наименование','ИНН','Регион','Телефоны','Почта','Сайт','Источник','Статус работы','Описание звонка','Этап']
            rows=[[r.get('name',''),r.get('inn',''),r.get('region',''),'\n'.join(r.get('phones',[])),'\n'.join(r.get('emails',[])),r.get('website',''),r.get('source',''),r['lead_status'],r['call_note'],r.get('stage','')] for r in items]
        else:raise HTTPException(404,'Выгрузка не найдена')
        stream=io.BytesIO()
        with xlsxwriter.Workbook(stream,{'in_memory':True,'strings_to_formulas':False,'strings_to_urls':False}) as book:
            sheet=book.add_worksheet('База');fmt=book.add_format({'bold':True,'bg_color':'#28364A','font_color':'white','text_wrap':True})
            sheet.write_row(0,0,headers,fmt);sheet.set_column(0,len(headers)-1,28);sheet.freeze_panes(1,1)
            for n,row in enumerate(rows,1):sheet.write_row(n,0,row)
            sheet.autofilter(0,0,len(rows),len(headers)-1)
        return Response(stream.getvalue(),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename={kind}.xlsx'})
    base=Path(web or Path(__file__).with_name('web'))
    if pack:app.mount('/pack',StaticFiles(directory=pack,html=True),name='pack')
    @app.get('/')
    def index():return FileResponse(base/'index.html')
    app.mount('/assets',StaticFiles(directory=base),name='assets')
    return app

# Version 1.3 uses the role-aware API; retained models above are not exposed.
from .api15 import create_app
