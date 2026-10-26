from datetime import datetime,timezone,timedelta
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID,uuid4
import io,json,os,threading,time,hashlib,re
import xlsxwriter
from fastapi import FastAPI,Request,Depends,HTTPException
from fastapi.responses import JSONResponse,Response,FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field,ConfigDict,field_validator
from psycopg.types.json import Jsonb
from psycopg.errors import UniqueViolation
from .database import Database,Conflict,NotFound
from .version import VERSION
from .auth import Authentication
from .search15 import SearchWorker
from .criteria import Criteria,DIVISIONS,SEGMENTS
from .checko import Checko,VerificationUnavailable
from . import host_secrets
from .presentation import summary
from source_catalog import SOURCES
ROLES={'logist':'Логист','manager':'Руководитель отдела','director':'Генеральный директор'}
class Input(BaseModel):model_config=ConfigDict(extra='forbid')
class Register(Input):
 first_name:str=Field(min_length=1,max_length=100);last_name:str=Field(min_length=1,max_length=100);password:str=Field(min_length=8,max_length=256)
 login:str|None=Field(default=None,pattern=r'^[a-zA-Z0-9._-]{3,64}$');requested_role:str=Field(default='logist',pattern=r'^(logist|manager|director)$')
 device_id:str=Field(min_length=1,max_length=128);computer_name:str=Field(default='',max_length=128)
class Login(Input):
 login:str=Field(min_length=1,max_length=64);password:str=Field(min_length=1,max_length=256);device_id:str=Field(min_length=1,max_length=128);computer_name:str=Field(default='',max_length=128)
class Search(Input):target:int=Field(default=10,ge=1,le=50);criteria:Criteria=Field(default_factory=Criteria)
class Lead(Input):status:str=Field(pattern=r'^(new|processing|refused|taken)$');called:bool;call_note:str=Field(default='',max_length=10000);revision:int=Field(ge=0)
class Stage(Input):stage:str=Field(max_length=500);revision:int=Field(ge=0)
class StageList(Input):stages:list[str]=Field(max_length=40)
class Route(Input):
 origin:str=Field(default='',max_length=200);destination:str=Field(default='',max_length=200);cargo:str=Field(default='',max_length=500);weight_kg:float|None=Field(default=None,ge=0,le=1_000_000);revision:int=Field(ge=0)
class Carrier(Input):
 inn:str=Field(default='',max_length=12)
 @field_validator('inn')
 @classmethod
 def carrier_inn(cls,value):
  value=value.strip()
  if value:
   if not re.fullmatch(r'[0-9]{10}|[0-9]{12}',value):raise ValueError('ИНН должен содержать 10 или 12 цифр')
   weights=([2,4,10,3,5,9,4,6,8],) if len(value)==10 else ([7,2,4,10,3,5,9,4,6,8],[3,7,2,4,10,3,5,9,4,6,8])
   if any(sum(int(d)*w for d,w in zip(value,ws))%11%10!=int(value[len(ws)]) for ws in weights):raise ValueError('Проверьте контрольные цифры ИНН')
  return value
 phone:str=Field(default='',max_length=200);email:str=Field(default='',max_length=300);work_terms:str=Field(default='',max_length=5000);vat_mode:str=Field(default='unknown',pattern='^(unknown|included|without)$');vat_rate:float|None=Field(default=None,ge=0,le=100)
 name:str=Field(min_length=1,max_length=500);contacts:str=Field(default='',max_length=5000);routes:str=Field(default='',max_length=5000);description:str=Field(default='',max_length=10000);revision:int|None=None
class Plan(Input):name:str=Field(min_length=1,max_length=200);project:dict;revision:int|None=None
class Active(Input):active:bool
class Role(Input):role:str=Field(pattern=r'^(logist|manager|director)$')
class Legacy(Input):companies:list[dict]=Field(max_length=200)
class Setting(Input):checko_api_key:str|None=Field(default=None,max_length=200)
class Activity(Input):
 kind:str=Field(pattern=r'^(view\.(search|clients|carriers|pack|team|history|rates|maps|tools|vat)|company\.view|external\.(calculator|signl|yandex|2gis|scales|cargo|tracking|clock|pdf|optimus)|route\.use|session\.exit|view\.(today|orders|tasks)|order\.view)$')
 entity:str=Field(default='',max_length=100);details:dict=Field(default_factory=dict)
class Quote(Input):
 origin:str=Field(min_length=1,max_length=200);destination:str=Field(min_length=1,max_length=200);vehicle:str=Field(default='',max_length=200)
 source:str=Field(pattern=r'^(VIP калькулятор|Сигнал|Перевозчик|Вручную)$');amount_rub:float=Field(ge=0,le=1_000_000_000);vat:str=Field(default='unknown',pattern='^(unknown|included|without)$');note:str=Field(default='',max_length=1000)
 as_of:datetime;valid_until:datetime|None=None;source_url:str=Field(default='',max_length=1000);vat_rate:float|None=Field(default=None,ge=0,le=100)
 vehicle_type:str=Field(pattern='^(tent|refrigerator|flatbed|lowbed|van|container)$');pricing_basis:str=Field(default='trip',pattern='^(trip|km)$')
 @field_validator('as_of','valid_until')
 @classmethod
 def dates(cls,v):
  if v and not v.tzinfo:raise ValueError('Дата должна содержать часовой пояс')
  return v

def create_app(database=None,web=None,pack=None,start_worker=True):
 db=database or Database(os.environ['NEXOL_DATABASE_URL']);db.initialize();auth=Authentication(db);checker=Checko(db);worker=SearchWorker(db,checker)
 @asynccontextmanager
 async def lifespan(app):
  if start_worker:worker.start()
  yield
  if start_worker:worker.stop()
  if database is None:db.close()
 app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None);app.state.db=db;app.state.auth=auth;app.state.worker=worker;app.state.checker=checker
 attempts={};rate_lock=threading.Lock()
 def token(request):
  h=request.headers.get('authorization','');return h[7:] if h.startswith('Bearer ') else ''
 def employee(request:Request):
  try:person=auth.authenticate(token(request),request.client.host)
  except PermissionError as ex:raise HTTPException(401,str(ex))
  request.state.user=person;return person
 def director(user=Depends(employee)):
  if user['role']!='director':raise HTTPException(403,'Доступ только генеральному директору')
  return user
 def manager(user=Depends(employee)):
  if user['role'] not in ('manager','director'):raise HTTPException(403,'Доступ только руководителю')
  return user
 def audit(user,kind,entity='',details=None,request=None):db.audit(user['id'] if user else None,kind,entity,details,request.client.host if request else '')
 def throttle(request,login):
  key=(request.client.host,login.casefold())
  with rate_lock:
   now=time.monotonic();values=[t for t in attempts.get(key,[]) if now-t<300]
   if len(values)>=12:raise HTTPException(429,'Слишком много попыток входа. Повторите через 5 минут.')
   attempts[key]=[*values,now]
   if len(attempts)>2000:
    for k in list(attempts):
     if now-attempts[k][-1]>300:attempts.pop(k,None)
 def visible_person(user,ident):
  with db.transaction(user['id']) as conn:p=conn.execute('SELECT id,role,department FROM employees WHERE id=%s',(ident,)).fetchone()
  if not p or not(user['role']=='director' or ident==user['id'] or (user['role']=='manager' and p['role']=='logist' and p['department']==user['department'])):raise NotFound('Сотрудник недоступен')
 def visible_company(user,ident):
  with db.transaction(user['id']) as conn:r=conn.execute('SELECT c.payload FROM companies c WHERE c.id=%s AND (%s OR EXISTS(SELECT 1 FROM employee_leads l WHERE l.company_id=c.id))',(ident,user['role']=='director')).fetchone()
  if not r:raise NotFound('Компания недоступна')
  return r['payload']
 for cls,code in ((Conflict,409),(NotFound,404),(ValueError,400),(VerificationUnavailable,503)):
  async def error(_,ex,code=code):return JSONResponse({'error':str(ex)},code)
  app.add_exception_handler(cls,error)
 @app.exception_handler(HTTPException)
 async def http_error(_,ex):return JSONResponse({'error':ex.detail},ex.status_code)
 @app.middleware('http')
 async def secure(request,call_next):
  try:size=int(request.headers.get('content-length','0') or 0)
  except ValueError:return JSONResponse({'error':'Некорректный запрос'},400)
  if size<0 or size>5*1024*1024:return JSONResponse({'error':'Слишком большой запрос'},413)
  response=await call_next(request);response.headers['X-Content-Type-Options']='nosniff';response.headers['Cache-Control']='no-store'
  response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; frame-src 'self'; frame-ancestors 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'"
  if response.status_code>=400 and request.url.path.startswith('/api/') and request.url.path not in ('/api/me','/api/state'):audit(getattr(request.state,'user',None),'request.denied',request.url.path,{'status':response.status_code},request)
  return response
 @app.get('/api/host-info')
 def host_info():return dict(app='nexsearch-host',protocol=1,version=VERSION,host_id=os.environ.get('NEXOL_HOST_ID','development'))
 @app.get('/api/catalog')
 def catalog():return dict(timezones=[{'offset':h,'label':f'UTC+{h} · МСК'+(f'{h-3:+d}' if h!=3 else '')} for h in range(2,13)],roles=ROLES,sectors=[{'code':k,'name':v} for k,v in DIVISIONS.items()],segments=[dict(id=k,**v) for k,v in SEGMENTS.items()])
 @app.post('/api/auth/register')
 def register(body:Register,request:Request):
  throttle(request,'register')
  if not body.first_name.strip() or not body.last_name.strip():raise ValueError('Заполните имя и фамилию')
  return auth.register(body.first_name.strip(),body.last_name.strip(),body.password,body.device_id,body.computer_name,request.client.host,body.login,body.requested_role)
 @app.post('/api/auth/login')
 def login(body:Login,request:Request):
  throttle(request,body.login)
  try:return auth.login(body.login,body.password,body.device_id,body.computer_name,request.client.host)
  except PermissionError as ex:audit(None,'account.login_failed','',{'login':body.login},request);raise HTTPException(401,str(ex))
 @app.post('/api/auth/logout')
 def logout(request:Request,user=Depends(employee)):audit(user,'account.logout',request=request);auth.logout(token(request));return {'ok':True}
 @app.get('/api/me')
 def me(user=Depends(employee)):return {'employee':user}
 @app.get('/api/state')
 def state(user=Depends(employee)):
  with db.transaction(user['id']) as conn:
   jobs=conn.execute('SELECT * FROM jobs WHERE employee_id=%s ORDER BY created_at DESC LIMIT 5',(user['id'],)).fetchall()
   count=conn.execute('SELECT count(*) AS n FROM companies' if user['role']=='director' else 'SELECT count(*) AS n FROM employee_leads WHERE employee_id=%s',() if user['role']=='director' else (user['id'],)).fetchone()['n']
   clients=conn.execute('SELECT count(*) AS n FROM employee_clients WHERE employee_id=%s AND active',(user['id'],)).fetchone()['n'];carriers=conn.execute('SELECT count(*) AS n FROM carriers').fetchone()['n']
   version=conn.execute("SELECT COALESCE(max(id),0) AS n FROM audit_events WHERE %s OR employee_id=%s OR kind LIKE 'carrier.%%' OR (%s AND nexsearch_can_read(employee_id))",(user['role']=='director',user['id'],user['role']=='manager')).fetchone()['n']
  return dict(employee=user,jobs=jobs,stats=dict(found=count,clients=clients,carriers=carriers),sources=SOURCES,version=hashlib.sha256((str(version)+json.dumps(jobs,default=str)).encode()).hexdigest()[:16])
 @app.get('/api/leads')
 def leads(user=Depends(employee)):
  if user['role']=='director':
   with db.transaction(user['id']) as conn:rows=conn.execute('SELECT c.id,c.payload,l.status AS lead_status,l.employee_id,e.login AS assigned_to,l.called,l.call_note,l.called_at,l.revision FROM companies c LEFT JOIN employee_leads l ON l.company_id=c.id LEFT JOIN employees e ON e.id=l.employee_id ORDER BY c.created_at DESC').fetchall()
   return {'companies':[dict(summary(r.pop('payload')),**r) for r in rows]}
  return {'companies':db.leads(user['id'])}
 @app.patch('/api/leads/{ident}')
 def edit_lead(ident:UUID,body:Lead,user=Depends(employee)):return db.update_lead(user['id'],ident,body.status,body.called,body.call_note,body.revision)
 @app.get('/api/clients')
 def clients(stage:str|None=None,order:str='recent',user=Depends(employee)):
  if order not in ('recent','stage','name'):raise ValueError('Неизвестная сортировка')
  return {'companies':db.leads(user['id'],clients=True,stage=stage,order=order)}
 @app.get('/api/clients/stages')
 def stages(user=Depends(employee)):
  with db.transaction(user['id']) as conn:
   saved=[r['name'] for r in conn.execute('SELECT name FROM workspace_stages WHERE employee_id=%s ORDER BY position',(user['id'],))];used=[r['stage'] for r in conn.execute('SELECT DISTINCT stage FROM employee_clients WHERE employee_id=%s AND active ORDER BY stage',(user['id'],))]
  return {'stages':list(dict.fromkeys((saved or ['В работе','Связаться','Предложение','Договор','Завершено'])+used))}
 @app.put('/api/clients/stages')
 def save_stages(body:StageList,user=Depends(employee)):
  names=list(dict.fromkeys(s.strip() for s in body.stages if s.strip()))
  if any(len(s)>500 for s in names):raise ValueError('Длинное название этапа')
  with db.transaction(user['id']) as conn:
   conn.execute('DELETE FROM workspace_stages WHERE employee_id=%s',(user['id'],))
   for n,name in enumerate(names):conn.execute('INSERT INTO workspace_stages VALUES(%s,%s,%s)',(user['id'],name,n))
  audit(user,'client.stages','',{'stages':names});return {'stages':names}
 @app.patch('/api/clients/{ident}/stage')
 def stage(ident:UUID,body:Stage,user=Depends(employee)):return db.update_stage(user['id'],ident,body.stage.strip(),body.revision)
 @app.patch('/api/clients/{ident}/route')
 def client_route(ident:UUID,body:Route,user=Depends(employee)):
  with db.transaction(user['id']) as conn:
   old=conn.execute('SELECT * FROM employee_clients WHERE company_id=%s AND employee_id=%s AND active FOR UPDATE',(ident,user['id'])).fetchone()
   if not old:raise NotFound('Клиент недоступен')
   if old['revision']!=body.revision:raise Conflict('Клиент уже изменён')
   result=conn.execute('UPDATE employee_clients SET origin=%s,destination=%s,cargo=%s,weight_kg=%s,revision=revision+1,updated_at=now() WHERE company_id=%s RETURNING *',(body.origin.strip(),body.destination.strip(),body.cargo,body.weight_kg,ident)).fetchone()
  audit(user,'client.route',ident,body.model_dump(exclude={'revision'}));return result
 @app.get('/api/search/preferences')
 def preferences(user=Depends(employee)):
  with db.transaction(user['id']) as conn:row=conn.execute('SELECT criteria FROM search_preferences WHERE employee_id=%s',(user['id'],)).fetchone()
  return {'criteria':row['criteria'] if row else Criteria().model_dump()}
 @app.put('/api/search/preferences')
 def save_preferences(body:Criteria,user=Depends(employee)):
  with db.transaction(user['id']) as conn:conn.execute('INSERT INTO search_preferences VALUES(%s,%s) ON CONFLICT(employee_id) DO UPDATE SET criteria=excluded.criteria',(user['id'],Jsonb(body.model_dump())))
  audit(user,'search.criteria','',body.model_dump());return {'criteria':body.model_dump()}
 @app.post('/api/search')
 def search(body:Search,user=Depends(employee)):
  try:
   with db.transaction(user['id']) as conn:row=conn.execute('INSERT INTO jobs(id,employee_id,target,criteria,message) VALUES(%s,%s,%s,%s,%s) RETURNING *',(uuid4(),user['id'],body.target,Jsonb(body.criteria.model_dump()),'В очереди хоста')).fetchone()
  except UniqueViolation:raise Conflict('Ваш поиск уже выполняется')
  audit(user,'search.start',row['id'],{'target':body.target,'criteria':body.criteria.model_dump()});worker.wake.set();return row
 @app.post('/api/search/{ident}/stop')
 def stop(ident:UUID,user=Depends(employee)):
  with db.transaction(user['id']) as conn:
   row=conn.execute("UPDATE jobs SET stop_requested=true,status='stopped',finished_at=now(),message='Поиск остановлен; найденные компании сохранены' WHERE id=%s AND employee_id=%s AND status IN ('queued','running') RETURNING status",(ident,user['id'])).fetchone()
   if not row:raise NotFound('Нет активного поиска')
   if row['status']=='queued':conn.execute("UPDATE jobs SET status='stopped',finished_at=now() WHERE id=%s",(ident,))
  worker.cancel(ident)
  audit(user,'search.stop',ident);return {'ok':True}
 @app.get('/api/company/{ident}/financial')
 def financial(ident:UUID,user=Depends(employee)):
  item=visible_company(user,ident);audit(user,'company.financial_view',ident);return {'financial':item.get('financial',{}),'verification':item.get('verification',{}),'company':item.get('checko_company',{})}
 @app.post('/api/company/{ident}/refresh')
 def refresh(ident:UUID,user=Depends(employee)):
  item=visible_company(user,ident);checker.verify(item['inn'],force=True);checker.enrich(item);db.ingest(item);audit(user,'company.refresh',ident);return {'financial':item['financial'],'verification':item['verification']}
 @app.get('/api/carriers')
 def carriers(user=Depends(employee)):
  with db.transaction(user['id']) as conn:return {'carriers':conn.execute("SELECT c.*,e.first_name||' '||e.last_name AS author FROM carriers c JOIN employees e ON e.id=c.created_by ORDER BY c.updated_at DESC").fetchall()}
 @app.post('/api/carriers')
 def add_carrier(body:Carrier,user=Depends(employee)):
  with db.transaction(user['id']) as conn:row=conn.execute('INSERT INTO carriers(id,name,inn,contacts,routes,description,phone,email,work_terms,vat_mode,vat_rate,created_by,updated_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',(uuid4(),body.name.strip(),body.inn,body.contacts,body.routes,body.description,body.phone,body.email,body.work_terms,body.vat_mode,body.vat_rate,user['id'],user['id'])).fetchone()
  audit(user,'carrier.create',row['id'],body.model_dump(exclude={'revision'}));return row
 @app.patch('/api/carriers/{ident}')
 def edit_carrier(ident:UUID,body:Carrier,user=Depends(employee)):
  with db.transaction(user['id']) as conn:
   old=conn.execute('SELECT * FROM carriers WHERE id=%s FOR UPDATE',(ident,)).fetchone()
   if not old:raise NotFound('Перевозчик не найден')
   if old['revision']!=body.revision:raise Conflict('Перевозчик изменён другим сотрудником')
   row=conn.execute('UPDATE carriers SET name=%s,inn=%s,contacts=%s,routes=%s,description=%s,phone=%s,email=%s,work_terms=%s,vat_mode=%s,vat_rate=%s,updated_by=%s,revision=revision+1,updated_at=now() WHERE id=%s RETURNING *',(body.name.strip(),body.inn if 'inn' in body.model_fields_set else old['inn'],body.contacts,body.routes,body.description,*[getattr(body,k) if k in body.model_fields_set else old[k] for k in ('phone','email','work_terms','vat_mode','vat_rate')],user['id'],ident)).fetchone()
  audit(user,'carrier.update',ident,body.model_dump(exclude={'revision'}));return row
 @app.get('/api/plans')
 def plans(user=Depends(employee)):
  with db.transaction(user['id']) as conn:return {'plans':conn.execute('SELECT id,name,revision,updated_at FROM loading_plans WHERE employee_id=%s ORDER BY updated_at DESC',(user['id'],)).fetchall()}
 @app.get('/api/plans/{ident}')
 def plan(ident:UUID,user=Depends(employee)):
  with db.transaction(user['id']) as conn:row=conn.execute('SELECT * FROM loading_plans WHERE id=%s',(ident,)).fetchone()
  if not row:raise NotFound('План недоступен')
  audit(user,'plan.open',ident);return row
 @app.post('/api/plans')
 def add_plan(body:Plan,user=Depends(employee)):
  with db.transaction(user['id']) as conn:row=conn.execute('INSERT INTO loading_plans(id,employee_id,name,project) VALUES(%s,%s,%s,%s) RETURNING id,name,revision',(uuid4(),user['id'],body.name,Jsonb(body.project))).fetchone()
  audit(user,'plan.create',row['id'],{'name':body.name});return row
 @app.put('/api/plans/{ident}')
 def edit_plan(ident:UUID,body:Plan,user=Depends(employee)):
  with db.transaction(user['id']) as conn:
   old=conn.execute('SELECT revision FROM loading_plans WHERE id=%s AND employee_id=%s FOR UPDATE',(ident,user['id'])).fetchone()
   if not old:raise NotFound('План недоступен')
   if old['revision']!=body.revision:raise Conflict('План уже изменён')
   row=conn.execute('UPDATE loading_plans SET name=%s,project=%s,revision=revision+1,updated_at=now() WHERE id=%s RETURNING id,name,revision',(body.name,Jsonb(body.project),ident)).fetchone()
  audit(user,'plan.update',ident,{'name':body.name});return row
 @app.get('/api/admin/employees')
 def employees(user=Depends(manager)):
  with db.transaction(user['id']) as conn:
   rows=conn.execute("SELECT e.id,e.login,e.first_name,e.last_name,e.active,e.is_admin,e.role,e.requested_role,e.department,e.approved,(SELECT max(d.last_seen) FROM devices d WHERE d.employee_id=e.id) AS last_seen,(SELECT count(*) FROM employee_leads l WHERE l.employee_id=e.id) AS found_count,(SELECT count(*) FROM employee_clients cl WHERE cl.employee_id=e.id AND cl.active) AS client_count FROM employees e WHERE %s OR e.id=%s OR (e.role='logist' AND e.department=%s) ORDER BY e.created_at",(user['role']=='director',user['id'],user['department'])).fetchall()
   devices=conn.execute('SELECT id,employee_id,computer_name,ip,last_seen FROM devices WHERE employee_id=ANY(%s)',([r['id'] for r in rows],)).fetchall()
  return {'employees':rows,'devices':devices}
 @app.get('/api/admin/employees/{ident}/clients')
 def team_clients(ident:UUID,user=Depends(manager)):
  visible_person(user,ident);audit(user,'employee.clients_view',ident);return {'companies':db.leads(ident,clients=True,actor=user['id'])}
 @app.patch('/api/admin/employees/{ident}/role')
 def role(ident:UUID,body:Role,user=Depends(director)):
  if ident==user['id'] and body.role!='director':raise ValueError('Нельзя понизить собственный доступ')
  with db.transaction(user['id']) as conn:row=conn.execute('UPDATE employees SET role=%s,requested_role=%s,is_admin=%s WHERE id=%s RETURNING id,role',(body.role,body.role,body.role=='director',ident)).fetchone()
  if not row:raise NotFound('Сотрудник не найден')
  audit(user,'employee.role',ident,{'role':body.role});return row
 @app.patch('/api/admin/employees/{ident}/active')
 def active(ident:UUID,body:Active,user=Depends(director)):
  if ident==user['id']:raise ValueError('Нельзя отключить собственный аккаунт')
  with db.transaction(user['id']) as conn:
   row=conn.execute('UPDATE employees SET active=%s WHERE id=%s RETURNING id,active',(body.active,ident)).fetchone()
   if not row:raise NotFound('Сотрудник не найден')
   if not body.active:conn.execute('DELETE FROM sessions WHERE employee_id=%s',(ident,))
  audit(user,'employee.active',ident,{'active':body.active});return row
 @app.get('/api/history')
 def history(employee_id:UUID|None=None,before:int|None=None,user=Depends(manager)):
  if employee_id:visible_person(user,employee_id)
  with db.transaction(user['id']) as conn:rows=conn.execute('SELECT a.*,e.first_name,e.last_name,e.login FROM audit_events a LEFT JOIN employees e ON e.id=a.employee_id WHERE (%s OR nexsearch_can_read(a.employee_id)) AND (%s::uuid IS NULL OR a.employee_id=%s) AND (%s::bigint IS NULL OR a.id<%s) ORDER BY a.id DESC LIMIT 150',(user['role']=='director',employee_id,employee_id,before,before)).fetchall()
  return {'events':rows,'next_before':rows[-1]['id'] if len(rows)==150 else None}
 @app.post('/api/activity')
 def activity(body:Activity,request:Request,user=Depends(employee)):
  if len(json.dumps(body.details))>3000:raise ValueError('Слишком подробное событие')
  detail={k:v for k,v in body.details.items() if k in ('origin','destination','service','name','vehicle') and isinstance(v,str)};audit(user,body.kind,body.entity,detail,request);return {'ok':True}
 @app.get('/api/settings')
 def settings(user=Depends(director)):return {'checko_configured':bool(checker.key()),'checko_usage':checker.metadata,'version':VERSION}
 @app.patch('/api/settings')
 def setting(body:Setting,user=Depends(director)):
  values=host_secrets.load()
  if body.checko_api_key is not None:values['checko_api_key']=body.checko_api_key.strip()
  host_secrets.save(values);audit(user,'host.settings','',{'checko_key_changed':body.checko_api_key is not None});return {'checko_configured':bool(values.get('checko_api_key'))}
 @app.get('/api/routes')
 def routes(user=Depends(employee)):
  with db.transaction(user['id']) as conn:
   clients=conn.execute("SELECT origin,destination,count(*) AS uses FROM employee_clients WHERE employee_id=%s AND active AND origin<>'' AND destination<>'' GROUP BY origin,destination",(user['id'],)).fetchall();quotes=conn.execute('SELECT * FROM route_quotes WHERE employee_id=%s ORDER BY created_at DESC LIMIT 200',(user['id'],)).fetchall();events=conn.execute("SELECT details FROM audit_events WHERE employee_id=%s AND kind='route.use' ORDER BY created_at DESC LIMIT 1000",(user['id'],)).fetchall();own=conn.execute('SELECT routes FROM carriers WHERE created_by=%s OR updated_by=%s',(user['id'],user['id'])).fetchall()
  combined={}
  def count(a,b,n=1):
   if a and b:combined.setdefault((a,b),{'origin':a,'destination':b,'uses':0});combined[(a,b)]['uses']+=n
  for r in clients:count(r['origin'],r['destination'],r['uses'])
  for r in events:count(r['details'].get('origin'),r['details'].get('destination'))
  for r in own:
   for line in r['routes'].splitlines():
    parts=re.split(r'\s*(?:→|—| - )\s*',line,maxsplit=1)
    if len(parts)==2:count(*parts)
  for q in quotes:count(q['origin'],q['destination'])
  result=sorted(combined.values(),key=lambda x:-x['uses'])[:20]
  for r in result:r['quotes']=[q for q in quotes if q['origin']==r['origin'] and q['destination']==r['destination']][:5]
  return {'routes':result,'quotes':quotes}
 @app.post('/api/routes/quotes')
 def quote(body:Quote,user=Depends(employee)):
  if body.as_of>datetime.now(timezone.utc)+timedelta(seconds=60):raise ValueError('Дата ставки не может быть в будущем')
  if body.valid_until and body.valid_until<body.as_of:raise ValueError('Срок действия раньше даты ставки')
  with db.transaction(user['id']) as conn:row=conn.execute('INSERT INTO route_quotes(id,employee_id,origin,destination,vehicle,source,amount_rub,vat,note,as_of,valid_until,source_url,vat_rate,vehicle_type,pricing_basis) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',(uuid4(),user['id'],body.origin,body.destination,body.vehicle,body.source,body.amount_rub,body.vat,body.note,body.as_of,body.valid_until,body.source_url,body.vat_rate,body.vehicle_type,body.pricing_basis)).fetchone()
  audit(user,'route.quote',row['id'],body.model_dump());return row
 @app.post('/api/import/legacy')
 def legacy(body:Legacy,user=Depends(employee)):
  added=0;skipped=0
  for item in body.companies:
   try:
    checker.enrich(item)
    if not item['verification']['active']:skipped+=1;continue
    ident,_,assigned=db.ingest(item,user['id'])
    if assigned:added+=1;called=bool(item.get('called'));db.update_lead(user['id'],ident,'taken' if item.get('lead_status')=='taken' and called else 'new',called,str(item.get('call_note',''))[:10000],0)
    else:skipped+=1
   except (ValueError,KeyError):skipped+=1
  audit(user,'data.import','',{'added':added,'skipped':skipped});return {'added':added,'skipped':skipped}
 @app.get('/api/export/{kind}')
 def export(kind:str,user=Depends(employee)):
  if kind=='carriers':
   items=carriers(user)['carriers'];headers=['Наименование','ИНН','Телефон','Почта','Условия работы','НДС','Ставка НДС, %','Другие контакты','Маршруты','Описание','Регионы работы'];rows=[[r['name'],r['inn'],r['phone'],r['email'],r['work_terms'],{'included':'НДС включён','without':'Без НДС','unknown':'Не уточнён'}.get(r['vat_mode'],r['vat_mode']),float(r['vat_rate']) if r['vat_rate'] is not None else None,r['contacts'],r['routes'],r['description'],r['regions']] for r in items]
  elif kind in ('leads','clients'):
   items=leads(user)['companies'] if kind=='leads' else clients(user=user)['companies'];headers=['Наименование','ИНН','Регион','Телефоны','Почта','Сайт','Источник','Юр. статус','Статус работы','Звонок','Этап','Откуда','Куда','Год отчёта','Выручка, ₽','Прибыль, ₽','Капитал, ₽','Транспортный налог, ₽','Год налога','Адрес','ОГРН','КПП','Проверка','Сегмент'];rows=[]
   for r in items:
    f=r.get('financial') or {};rows.append([r.get('name',''),r.get('inn',''),r.get('region',''),'\n'.join(r.get('phones') or []),'\n'.join(r.get('emails') or []),r.get('website',''),r.get('source',''),r.get('status',''),r.get('lead_status','new'),r.get('call_note',''),r.get('stage',''),r.get('origin',''),r.get('destination',''),f.get('year'),f.get('revenue_rub'),f.get('profit_rub'),f.get('capital_rub'),f.get('transport_tax_rub'),f.get('tax_year'),r.get('legal_address',''),r.get('ogrn',''),r.get('kpp',''),(r.get('verification') or {}).get('checked_at',''),r.get('segment','')])
  else:raise NotFound('Выгрузка недоступна')
  stream=io.BytesIO()
  with xlsxwriter.Workbook(stream,{'in_memory':True,'strings_to_formulas':False,'strings_to_urls':False}) as book:
   sheet=book.add_worksheet('База');fmt=book.add_format({'bold':True,'bg_color':'#37316b','font_color':'white','text_wrap':True});sheet.write_row(0,0,headers,fmt);sheet.set_column(0,len(headers)-1,28);sheet.freeze_panes(1,1)
   for n,row in enumerate(rows,1):sheet.write_row(n,0,row)
   sheet.autofilter(0,0,len(rows),len(headers)-1)
   if kind=='carriers':
    fleet=book.add_worksheet('Автомобили');fleet.write_row(0,0,['Перевозчик','Автомобиль','Тип','Грузоподъёмность, т','Объём, м³','Длина, м','Ширина, м','Высота, м','Примечание'],fmt);fleet.set_column(0,8,25);fleet.freeze_panes(1,1);n=0
    from .api14 import VEHICLES
    for carrier in items:
     for v in carrier['vehicles']:
      n+=1;fleet.write_row(n,0,[carrier['name'],v['name'],VEHICLES.get(v['kind'],v['kind']),v['payload_kg']/1000,v.get('volume_m3') or v['length_mm']*v['width_mm']*v['height_mm']/1e9,v['length_mm']/1000,v['width_mm']/1000,v['height_mm']/1000,v.get('notes','')])
    fleet.autofilter(0,0,n,8)
  audit(user,'data.export',kind,{'rows':len(rows)});return Response(stream.getvalue(),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename={kind}.xlsx'})
 base=Path(web or Path(__file__).with_name('web'))
 if pack:app.mount('/pack',StaticFiles(directory=pack,html=True),name='pack')
 @app.get('/')
 def index():return FileResponse(base/'index.html')
 app.mount('/assets',StaticFiles(directory=base),name='assets');return app
