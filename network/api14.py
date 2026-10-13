from datetime import datetime,timezone,timedelta
from pathlib import Path
from uuid import UUID,uuid4
from zoneinfo import ZoneInfo
import json,os
from fastapi import Depends,HTTPException,Request
from pydantic import Field,field_validator
from psycopg.types.json import Jsonb
from .api13 import create_app as base_app,Input
from .version import VERSION
from .database import NotFound,Conflict
from .auth import digest,public_employee
from .criteria import Criteria
from .rates import moment,margin,comparable,freshness,city_key

STATES={'new':'Новая','costing':'Расчёт','offer':'Предложение','agreed':'Согласована','loading':'Погрузка','on_route':'В пути','done':'Завершена','cancelled':'Отменена'}
VEHICLES={'tent':'Тент','refrigerator':'Рефрижератор','flatbed':'Бортовой','lowbed':'Трал','van':'Фургон','container':'Контейнеровоз'}
class Cargo(Input):
 name:str=Field(min_length=1,max_length=200);L:float=Field(gt=0,le=100000);W:float=Field(gt=0,le=20000);H:float=Field(gt=0,le=20000)
 weight:float=Field(ge=0,le=500000);qty:int=Field(default=1,ge=1,le=10000);tiers:int=Field(default=1,ge=1,le=100)
 rotatable:bool=True;vertical_rotation:bool=False;group:str=Field(default='',max_length=100);stop:int=Field(default=1,ge=1,le=100)
 cg:list[float]|None=Field(default=None,min_length=3,max_length=3)
 @field_validator('cg')
 @classmethod
 def cog(cls,v):
  if v and any(not 0<=x<=1 for x in v):raise ValueError('Центр массы задаётся долями размера от 0 до 1')
  return v
class Cost(Input):
 name:str=Field(min_length=1,max_length=200);amount:float=Field(ge=0,le=1e9);vat:str=Field(default='unknown',pattern='^(unknown|included|without)$');vat_rate:float|None=Field(default=None,ge=0,le=100)
class Order(Input):
 company_id:UUID;title:str=Field(min_length=1,max_length=300);origin:str=Field(min_length=1,max_length=200);destination:str=Field(min_length=1,max_length=200)
 status:str=Field(default='new',pattern='^('+ '|'.join(STATES)+')$');pickup_at:datetime|None=None;delivery_at:datetime|None=None
 cargo:list[Cargo]=Field(default_factory=list,max_length=500);vehicle_type:str=Field(default='',pattern='^('+ '|'.join(VEHICLES)+'|)$');notes:str=Field(default='',max_length=10000)
 carrier_id:UUID|None=None;client_price:float|None=Field(default=None,ge=0,le=1e9);carrier_price:float|None=Field(default=None,ge=0,le=1e9)
 client_vat:str=Field(default='unknown',pattern='^(unknown|included|without)$');carrier_vat:str=Field(default='unknown',pattern='^(unknown|included|without)$')
 client_vat_rate:float|None=Field(default=None,ge=0,le=100);carrier_vat_rate:float|None=Field(default=None,ge=0,le=100)
 costs:list[Cost]=Field(default_factory=list,max_length=100);revision:int|None=Field(default=None,ge=0)
 @field_validator('pickup_at','delivery_at')
 @classmethod
 def dates(cls,v):
  if v and v.tzinfo is None:raise ValueError('Дата должна содержать часовой пояс')
  return v
class Call(Input):
 company_id:UUID;contact_name:str=Field(default='',max_length=200);phone:str=Field(default='',max_length=100)
 result:str=Field(min_length=1,max_length=300);description:str=Field(default='',max_length=10000)
 called_at:datetime;next_contact_at:datetime|None=None;order_id:UUID|None=None
 @field_validator('called_at','next_contact_at')
 @classmethod
 def dates(cls,v):
  if v and v.tzinfo is None:raise ValueError('Дата должна содержать часовой пояс')
  if v and v>datetime.now(timezone.utc)+timedelta(days=3650):raise ValueError('Проверьте дату')
  return v
class Contact(Input):
 company_id:UUID;name:str=Field(min_length=1,max_length=200);position:str=Field(default='',max_length=200);phones:str=Field(default='',max_length=1000);email:str=Field(default='',max_length=300);description:str=Field(default='',max_length=3000);revision:int|None=None
class Task(Input):
 company_id:UUID|None=None;order_id:UUID|None=None;title:str=Field(min_length=1,max_length=300);description:str=Field(default='',max_length=5000)
 due_at:datetime;priority:str=Field(default='normal',pattern='^(normal|high)$');status:str=Field(default='open',pattern='^(open|done|cancelled)$');revision:int|None=None
 @field_validator('due_at')
 @classmethod
 def dates(cls,v):
  if not v.tzinfo:raise ValueError('Дата должна содержать часовой пояс')
  return v
class FleetVehicle(Input):
 name:str=Field(min_length=1,max_length=200);kind:str=Field(pattern='^('+ '|'.join(VEHICLES)+')$')
 length_mm:float=Field(gt=0,le=100000);width_mm:float=Field(gt=0,le=20000);height_mm:float=Field(gt=0,le=20000);payload_kg:float=Field(gt=0,le=500000)
 notes:str=Field(default='',max_length=2000)
class Fleet(Input):vehicles:list[FleetVehicle]=Field(max_length=100);regions:str=Field(default='',max_length=3000);revision:int=Field(ge=0)
class Feedback(Input):carrier_id:UUID;order_id:UUID|None=None;rating:int=Field(ge=1,le=5);description:str=Field(min_length=1,max_length=5000)
class Offer(Input):
 carrier_id:UUID|None=None;amount_rub:float=Field(ge=0,le=1e9);vehicle_type:str=Field(pattern='^('+ '|'.join(VEHICLES)+')$')
 vat:str=Field(pattern='^(unknown|included|without)$');vat_rate:float|None=Field(default=None,ge=0,le=100)
 as_of:datetime;valid_until:datetime|None=None;source:str=Field(min_length=1,max_length=200);source_url:str=Field(default='',max_length=1000);description:str=Field(default='',max_length=5000)
 @field_validator('as_of','valid_until')
 @classmethod
 def dates(cls,v):
  if v and not v.tzinfo:raise ValueError('Дата ставки должна содержать часовой пояс')
  return v
class Preset(Input):name:str=Field(min_length=1,max_length=200);criteria:Criteria
class Approval(Input):approved:bool
class LinkPlan(Input):plan_id:UUID;revision:int=Field(ge=0)
class RouteRequest(Input):from_id:str=Field(min_length=1,max_length=20);to_id:str=Field(min_length=1,max_length=20)

def create_app(database=None,web=None,pack=None,start_worker=True):
 app=base_app(database,web,pack,start_worker);db=app.state.db;auth=app.state.auth
 from .routing import Routing
 router=Routing(db)
 from .backups import Backups
 from contextlib import asynccontextmanager
 app.state.backups=Backups(db);old_lifespan=app.router.lifespan_context
 @asynccontextmanager
 async def lifespan(application):
  async with old_lifespan(application):
   if start_worker:application.state.backups.start()
   yield
   application.state.backups.close()
 app.router.lifespan_context=lifespan
 def employee(request:Request):
  token=request.headers.get('authorization','')
  try:user=auth.authenticate(token[7:] if token.startswith('Bearer ') else '',request.client.host)
  except PermissionError as ex:raise HTTPException(401,str(ex))
  request.state.user=user;return user
 def director(user=Depends(employee)):
  if user['role']!='director':raise HTTPException(403,'Доступ только генеральному директору')
  return user
 def actor(user,owner=None):
  target=owner or user['id']
  if str(target)!=str(user['id']):
   with db.transaction(user['id']) as conn:
    if not conn.execute('SELECT nexsearch_can_read(%s) AS allowed',(target,)).fetchone()['allowed']:raise NotFound('Рабочая база недоступна')
  return target
 def owned_company(user,ident,write=True):
  with db.transaction(user['id']) as conn:
   r=conn.execute('SELECT c.id FROM companies c JOIN employee_leads l ON l.company_id=c.id WHERE c.id=%s AND (%s OR l.employee_id=%s)',(ident,not write,user['id'])).fetchone()
  if not r:raise NotFound('Компания недоступна')
 def order_row(user,ident,write=False):
  with db.transaction(user['id']) as conn:r=conn.execute('SELECT * FROM shipment_orders WHERE id=%s AND (%s OR employee_id=%s)',(ident,not write,user['id'])).fetchone()
  if not r:raise NotFound('Заявка недоступна')
  return r
 def audit(user,kind,ident,details):db.audit(user['id'],kind,ident,details)
 def validate_order(user,body):
  owned_company(user,body.company_id)
  if body.delivery_at and body.pickup_at and body.delivery_at<body.pickup_at:raise ValueError('Выгрузка не может предшествовать погрузке')
  if body.carrier_id:
   with db.transaction(user['id']) as conn:
    if not conn.execute('SELECT 1 FROM carriers WHERE id=%s',(body.carrier_id,)).fetchone():raise NotFound('Перевозчик не найден')
 def order_fields(body):
  values=body.model_dump(exclude={'revision'})
  for key in ('cargo','costs'):values[key]=Jsonb(values[key])
  return values
 def enrich_order(row):return dict(row,margin=margin(row))

 @app.get('/api/workspace/catalog')
 def catalog(user=Depends(employee)):return {'order_statuses':STATES,'vehicle_types':VEHICLES,'version':VERSION}
 @app.get('/api/auth/status')
 def status(request:Request):
  token=request.headers.get('authorization','')
  if not token.startswith('Bearer '):raise HTTPException(401,'Требуется вход')
  with db.transaction(admin=True) as conn:row=conn.execute('SELECT e.* FROM sessions s JOIN employees e ON e.id=s.employee_id WHERE s.token_hash=%s AND s.expires_at>now() AND e.active',(digest(token[7:]),)).fetchone()
  if not row:raise HTTPException(401,'Сеанс завершён')
  return {'employee':public_employee(row)}
 @app.patch('/api/admin/employees/{ident}/approval')
 def approve(ident:UUID,body:Approval,user=Depends(director)):
  if str(ident)==str(user['id']) and not body.approved:raise ValueError('Нельзя закрыть свой доступ')
  with db.transaction(user['id']) as conn:
   row=conn.execute('UPDATE employees SET approved=%s WHERE id=%s RETURNING id,approved',(body.approved,ident)).fetchone()
  if not row:raise NotFound('Сотрудник не найден')
  audit(user,'employee.approval',ident,{'approved':body.approved});return row
 @app.get('/api/orders')
 def orders(employee_id:UUID|None=None,status:str|None=None,user=Depends(employee)):
  owner=actor(user,employee_id)
  with db.transaction(user['id']) as conn:rows=conn.execute("SELECT o.*,c.name AS company_name,ca.name AS carrier_name FROM shipment_orders o JOIN companies c ON c.id=o.company_id LEFT JOIN carriers ca ON ca.id=o.carrier_id WHERE o.employee_id=%s AND (%s::text IS NULL OR o.status=%s) ORDER BY o.updated_at DESC LIMIT 1000",(owner,status,status)).fetchall()
  return {'orders':[enrich_order(r) for r in rows]}
 @app.post('/api/orders')
 def create_order(body:Order,user=Depends(employee)):
  validate_order(user,body);values=order_fields(body);ident=uuid4()
  from psycopg import sql
  with db.transaction(user['id']) as conn:
   row=conn.execute(sql.SQL('INSERT INTO shipment_orders(id,employee_id,{}) VALUES(%s,%s,{}) RETURNING *').format(sql.SQL(',').join(map(sql.Identifier,values)),sql.SQL(',').join(sql.Placeholder() for _ in values)),(ident,user['id'],*values.values())).fetchone()
  audit(user,'order.create',ident,body.model_dump(mode='json',exclude={'revision'}));return enrich_order(row)
 @app.get('/api/orders/{ident}')
 def get_order(ident:UUID,user=Depends(employee)):return enrich_order(order_row(user,ident))
 @app.put('/api/orders/{ident}')
 def edit_order(ident:UUID,body:Order,user=Depends(employee)):
  old=order_row(user,ident,True);validate_order(user,body)
  if body.revision!=old['revision']:raise Conflict('Заявка уже изменена. Откройте её заново.')
  from psycopg import sql
  values=order_fields(body)
  with db.transaction(user['id']) as conn:
   row=conn.execute(sql.SQL('UPDATE shipment_orders SET {},revision=revision+1,updated_at=now() WHERE id=%s AND employee_id=%s AND revision=%s RETURNING *').format(sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(k)) for k in values)),(*values.values(),ident,user['id'],body.revision)).fetchone()
  if not row:raise Conflict('Заявка уже изменена')
  audit(user,'order.update',ident,{'before':{k:old[k] for k in values},'after':body.model_dump(mode='json',exclude={'revision'})});return enrich_order(row)
 @app.post('/api/orders/{ident}/plan')
 def link_plan(ident:UUID,body:LinkPlan,user=Depends(employee)):
  order_row(user,ident,True)
  with db.transaction(user['id']) as conn:
   if not conn.execute('SELECT 1 FROM loading_plans WHERE id=%s AND employee_id=%s',(body.plan_id,user['id'])).fetchone():raise NotFound('План недоступен')
   row=conn.execute('UPDATE shipment_orders SET loading_plan_id=%s,revision=revision+1,updated_at=now() WHERE id=%s AND employee_id=%s AND revision=%s RETURNING *',(body.plan_id,ident,user['id'],body.revision)).fetchone()
  if not row:raise Conflict('Заявка уже изменена')
  audit(user,'order.plan',ident,{'plan_id':str(body.plan_id)});return enrich_order(row)
 @app.get('/api/orders/{ident}/quotes')
 def offers(ident:UUID,user=Depends(employee)):
  order=order_row(user,ident)
  with db.transaction(user['id']) as conn:rows=conn.execute('SELECT q.*,c.name AS carrier_name FROM order_quotes q LEFT JOIN carriers c ON c.id=q.carrier_id WHERE q.order_id=%s ORDER BY q.as_of DESC',(ident,)).fetchall()
  return {'quotes':[dict(r,**comparable(order,r)) for r in rows]}
 @app.post('/api/orders/{ident}/quotes')
 def create_offer(ident:UUID,body:Offer,user=Depends(employee)):
  order=order_row(user,ident,True)
  if body.as_of>datetime.now(timezone.utc)+timedelta(seconds=60):raise ValueError('Дата ставки не может находиться в будущем')
  if body.valid_until and body.valid_until<body.as_of:raise ValueError('Проверьте срок действия ставки')
  values=body.model_dump();quote_id=uuid4()
  with db.transaction(user['id']) as conn:
   row=conn.execute('INSERT INTO order_quotes(id,employee_id,order_id,carrier_id,amount_rub,vehicle_type,vat,vat_rate,as_of,valid_until,source,source_url,description) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',(quote_id,user['id'],ident,*[values[k] for k in ('carrier_id','amount_rub','vehicle_type','vat','vat_rate','as_of','valid_until','source','source_url','description')])).fetchone()
  audit(user,'order.quote',ident,body.model_dump(mode='json'));return dict(row,**comparable(order,row))
 @app.get('/api/calls')
 def calls(company_id:UUID|None=None,employee_id:UUID|None=None,user=Depends(employee)):
  owner=actor(user,employee_id)
  with db.transaction(user['id']) as conn:rows=conn.execute('SELECT cr.*,c.name AS company_name FROM call_records cr JOIN companies c ON c.id=cr.company_id WHERE cr.employee_id=%s AND (%s::uuid IS NULL OR cr.company_id=%s) ORDER BY cr.called_at DESC LIMIT 1000',(owner,company_id,company_id)).fetchall()
  return {'calls':rows}
 @app.post('/api/calls')
 def add_call(body:Call,user=Depends(employee)):
  owned_company(user,body.company_id)
  if body.called_at>datetime.now(timezone.utc)+timedelta(seconds=60):raise ValueError('Дата звонка не может находиться в будущем')
  if body.order_id and order_row(user,body.order_id,True)['company_id']!=body.company_id:raise ValueError('Звонок и заявка относятся к разным клиентам')
  with db.transaction(user['id']) as conn:
   row=conn.execute('INSERT INTO call_records(id,employee_id,company_id,contact_name,phone,result,description,called_at,next_contact_at,order_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',(uuid4(),user['id'],body.company_id,body.contact_name,body.phone,body.result,body.description,body.called_at,body.next_contact_at,body.order_id)).fetchone()
   conn.execute("UPDATE employee_leads SET called=true,called_at=GREATEST(COALESCE(called_at,%s),%s),call_note=%s,revision=revision+1,updated_at=now() WHERE company_id=%s AND employee_id=%s",(body.called_at,body.called_at,body.result+'\n'+body.description,body.company_id,user['id']))
   if body.next_contact_at:conn.execute('INSERT INTO followup_tasks(id,employee_id,company_id,order_id,title,description,due_at) VALUES(%s,%s,%s,%s,%s,%s,%s)',(uuid4(),user['id'],body.company_id,body.order_id,'Перезвонить: '+body.contact_name,body.result,body.next_contact_at))
  audit(user,'call.create',row['id'],body.model_dump(mode='json'));return row
 @app.get('/api/contacts')
 def contacts(company_id:UUID,user=Depends(employee)):
  owned_company(user,company_id,False)
  with db.transaction(user['id']) as conn:return {'contacts':conn.execute('SELECT * FROM company_contacts WHERE company_id=%s ORDER BY name',(company_id,)).fetchall()}
 @app.post('/api/contacts')
 def contact(body:Contact,user=Depends(employee)):
  owned_company(user,body.company_id)
  with db.transaction(user['id']) as conn:row=conn.execute('INSERT INTO company_contacts(id,employee_id,company_id,name,position,phones,email,description) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',(uuid4(),user['id'],body.company_id,body.name,body.position,body.phones,body.email,body.description)).fetchone()
  audit(user,'contact.create',row['id'],body.model_dump(mode='json'));return row
 @app.get('/api/tasks')
 def tasks(employee_id:UUID|None=None,user=Depends(employee)):
  owner=actor(user,employee_id)
  with db.transaction(user['id']) as conn:return {'tasks':conn.execute('SELECT t.*,c.name AS company_name FROM followup_tasks t LEFT JOIN companies c ON c.id=t.company_id WHERE t.employee_id=%s ORDER BY t.status,t.due_at LIMIT 1000',(owner,)).fetchall()}
 def save_task(body,user,ident=None):
  if body.company_id:owned_company(user,body.company_id)
  if body.order_id:order_row(user,body.order_id,True)
  with db.transaction(user['id']) as conn:
   if ident:
    row=conn.execute("UPDATE followup_tasks SET company_id=%s,order_id=%s,title=%s,description=%s,due_at=%s,status=%s,priority=%s,revision=revision+1,completed_at=CASE WHEN %s='done' THEN now() ELSE NULL END WHERE id=%s AND employee_id=%s AND revision=%s RETURNING *",(body.company_id,body.order_id,body.title,body.description,body.due_at,body.status,body.priority,body.status,ident,user['id'],body.revision)).fetchone()
    if not row:raise Conflict('Задача недоступна или уже изменена')
   else:row=conn.execute('INSERT INTO followup_tasks(id,employee_id,company_id,order_id,title,description,due_at,status,priority) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',(uuid4(),user['id'],body.company_id,body.order_id,body.title,body.description,body.due_at,body.status,body.priority)).fetchone()
  audit(user,'task.update' if ident else 'task.create',row['id'],body.model_dump(mode='json'));return row
 @app.post('/api/tasks')
 def add_task(body:Task,user=Depends(employee)):return save_task(body,user)
 @app.put('/api/tasks/{ident}')
 def edit_task(ident:UUID,body:Task,user=Depends(employee)):return save_task(body,user,ident)
 @app.get('/api/today')
 def today(employee_id:UUID|None=None,user=Depends(employee)):
  owner=actor(user,employee_id);now=datetime.now(timezone.utc);local=now.astimezone(ZoneInfo('Europe/Moscow'));start=local.replace(hour=0,minute=0,second=0,microsecond=0);end=start+timedelta(days=1)
  with db.transaction(user['id']) as conn:
   pending=conn.execute("SELECT t.*,c.name AS company_name FROM followup_tasks t LEFT JOIN companies c ON c.id=t.company_id WHERE t.employee_id=%s AND t.status='open' AND t.due_at<%s ORDER BY t.due_at LIMIT 100",(owner,end)).fetchall()
   loading=conn.execute("SELECT o.*,c.name AS company_name FROM shipment_orders o JOIN companies c ON c.id=o.company_id WHERE o.employee_id=%s AND o.status NOT IN ('done','cancelled') AND o.pickup_at BETWEEN %s AND %s ORDER BY o.pickup_at LIMIT 100",(owner,start,start+timedelta(days=7))).fetchall()
   counts=conn.execute("SELECT count(*) FILTER(WHERE status NOT IN ('done','cancelled')) AS active,count(*) FILTER(WHERE status='done') AS done FROM shipment_orders WHERE employee_id=%s",(owner,)).fetchone()
  return {'tasks':pending,'upcoming_loadings':loading,'orders':counts,'time_zone':'Europe/Moscow','server_time':now}
 @app.put('/api/carriers/{ident}/fleet')
 def fleet(ident:UUID,body:Fleet,user=Depends(employee)):
  with db.transaction(user['id']) as conn:row=conn.execute('UPDATE carriers SET vehicles=%s,regions=%s,revision=revision+1,updated_by=%s,updated_at=now() WHERE id=%s AND revision=%s RETURNING *',(Jsonb([v.model_dump() for v in body.vehicles]),body.regions,user['id'],ident,body.revision)).fetchone()
  if not row:raise Conflict('Перевозчик недоступен или уже изменён')
  audit(user,'carrier.fleet',ident,body.model_dump());return row
 @app.post('/api/carriers/feedback')
 def feedback(body:Feedback,user=Depends(employee)):
  if body.order_id:order_row(user,body.order_id,True)
  with db.transaction(user['id']) as conn:row=conn.execute('INSERT INTO carrier_feedback(id,employee_id,carrier_id,order_id,rating,description) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *',(uuid4(),user['id'],body.carrier_id,body.order_id,body.rating,body.description)).fetchone()
  audit(user,'carrier.feedback',body.carrier_id,body.model_dump(mode='json'));return row
 @app.get('/api/orders/{ident}/carriers')
 def matching(ident:UUID,user=Depends(employee)):
  order=order_row(user,ident);cargo=order['cargo'];mass=sum(c['weight']*c['qty'] for c in cargo);results=[]
  with db.transaction(user['id']) as conn:
   carriers=conn.execute('SELECT * FROM carriers ORDER BY updated_at DESC').fetchall();feedback=conn.execute('SELECT carrier_id,avg(rating) AS rating,count(*) AS count FROM carrier_feedback GROUP BY carrier_id').fetchall()
  ratings={str(r['carrier_id']):r for r in feedback}
  for carrier in carriers:
   vehicle_results=[]
   for v in carrier['vehicles']:
    reasons=[]
    if order['vehicle_type'] and v['kind']!=order['vehicle_type']:reasons.append('Другой тип машины')
    if mass>v['payload_kg']:reasons.append('Превышение грузоподъёмности')
    for c in cargo:
     footprint=min(c['L']<=v['length_mm'] and c['W']<=v['width_mm'],True) if not c.get('rotatable') else ((c['L']<=v['length_mm'] and c['W']<=v['width_mm']) or (c['W']<=v['length_mm'] and c['L']<=v['width_mm']))
     if not footprint or c['H']>v['height_mm']:reasons.append('Не проходит по габаритам: '+c['name'])
    vehicle_results.append(dict(v,fits=not reasons,reasons=list(dict.fromkeys(reasons))))
   route_text=city_key(carrier['routes']);route_match=city_key(order['origin']) in route_text and city_key(order['destination']) in route_text
   score=(50 if any(v['fits'] for v in vehicle_results) else 0)+(30 if route_match else 0)
   results.append(dict(carrier,match_score=score,route_match=route_match,vehicle_matches=vehicle_results,own_feedback=ratings.get(str(carrier['id']))))
  return {'carriers':sorted(results,key=lambda r:-r['match_score']),'cargo_weight_kg':mass,'message':'Габариты проверены для отдельных мест; совместное размещение проверяется в PackOnTruck. Направление перевозчик подтверждает отдельно.'}
 @app.get('/api/search/presets')
 def presets(user=Depends(employee)):
  with db.transaction(user['id']) as conn:return {'presets':conn.execute('SELECT * FROM search_presets WHERE employee_id=%s ORDER BY created_at DESC',(user['id'],)).fetchall()}
 @app.post('/api/search/presets')
 def create_preset(body:Preset,user=Depends(employee)):
  with db.transaction(user['id']) as conn:row=conn.execute('INSERT INTO search_presets VALUES(%s,%s,%s,%s,now()) RETURNING *',(uuid4(),user['id'],body.name,Jsonb(body.criteria.model_dump()))).fetchone()
  audit(user,'search.preset',row['id'],body.model_dump());return row
 @app.delete('/api/search/presets/{ident}')
 def delete_preset(ident:UUID,user=Depends(employee)):
  with db.transaction(user['id']) as conn:row=conn.execute('DELETE FROM search_presets WHERE id=%s AND employee_id=%s RETURNING id',(ident,user['id'])).fetchone()
  if not row:raise NotFound('Набор недоступен')
  audit(user,'search.preset_delete',ident,{});return {'ok':True}
 @app.get('/api/diagnostics')
 def diagnostics(user=Depends(employee)):
  return {'host_id':os.environ.get('NEXOL_HOST_ID'),'version':VERSION,'database':bool(db.pool),'checko_configured':bool(app.state.checker.key()),'protocol':1,'time':datetime.now(timezone.utc),'discovery':'mDNS UDP 5353','transport':'Pinned HTTPS TCP 8743'}
 @app.get('/api/places')
 def places(q:str,user=Depends(employee)):
  if len(q)>200:raise ValueError('Слишком длинное название')
  return {'places':router.search(q),'source':'GeoNames','source_url':'https://www.geonames.org/','license':'CC BY'}
 @app.post('/api/orders/{ident}/route')
 def reference_route(ident:UUID,body:RouteRequest,user=Depends(employee)):
  order_row(user,ident,True);result=router.route(body.from_id,body.to_id)
  with db.transaction(user['id']) as c:c.execute('UPDATE shipment_orders SET route=%s,revision=revision+1,updated_at=now() WHERE id=%s AND employee_id=%s',(Jsonb(result),ident,user['id']))
  audit(user,'order.route',ident,{'origin':result['origin'],'destination':result['destination'],'distance_km':result['distance_km'],'source':result['source']});return result
 @app.get('/api/rates/history')
 def rate_history(origin:str='',destination:str='',vehicle_type:str='',user=Depends(employee)):
  with db.transaction(user['id']) as c:
   rows=c.execute("SELECT q.*,o.origin,o.destination,c.name AS carrier_name FROM order_quotes q JOIN shipment_orders o ON o.id=q.order_id LEFT JOIN carriers c ON c.id=q.carrier_id WHERE q.employee_id=%s AND (%s='' OR o.origin ILIKE %s) AND (%s='' OR o.destination ILIKE %s) AND (%s='' OR q.vehicle_type=%s) ORDER BY q.as_of DESC LIMIT 200",(user['id'],origin,origin,destination,destination,vehicle_type,vehicle_type)).fetchall()
   standalone=c.execute("SELECT *,note AS description FROM route_quotes WHERE employee_id=%s AND (%s='' OR origin ILIKE %s) AND (%s='' OR destination ILIKE %s) AND (%s='' OR vehicle_type=%s) ORDER BY as_of DESC LIMIT 200",(user['id'],origin,origin,destination,destination,vehicle_type,vehicle_type)).fetchall()
  rows+=standalone;rows.sort(key=lambda x:x['as_of'],reverse=True);rows=rows[:200]
  for row in rows:row['freshness']=freshness(row['as_of'],row['valid_until'],date_precision=row.get('date_precision','time'))
  return {'quotes':rows,'automatic_market_provider':False,'message':'Свежие подтверждённые предложения перевозчиков. Рыночные калькуляторы открываются отдельно; их цифры сохраняются с датой источника.'}
 @app.get('/api/events')
 async def events(request:Request,user=Depends(employee)):
  from fastapi.responses import StreamingResponse
  import asyncio
  try:position=max(0,int(request.headers.get('last-event-id','0')))
  except ValueError:position=0
  token=request.headers.get('authorization','')[7:]
  async def stream():
   nonlocal position,user
   count=0
   while not await request.is_disconnected():
    if count%10==0:
     try:user=await asyncio.to_thread(auth.authenticate,token,request.client.host)
     except PermissionError:
      yield 'event: access\ndata: {"revoked":true}\n\n';return
    rows,position=db.events.changes(user,position)
    for event in rows:yield 'id: '+str(event['version'])+'\nevent: change\ndata: '+json.dumps(event)+'\n\n'
    if count%10==0:yield ': heartbeat\n\n'
    count+=1;await asyncio.sleep(1)
  return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
 @app.get('/api/admin/backups')
 def backup_list(user=Depends(director)):
  with db.transaction(user['id']) as c:rows=c.execute('SELECT id,status,size_bytes,sha256,verified_at,error,created_at FROM backup_records ORDER BY created_at DESC LIMIT 100').fetchall()
  return {'backups':rows,'automatic_available':app.state.backups.available()}
 @app.post('/api/admin/backups')
 def create_backup(user=Depends(director)):return app.state.backups.create(user['id'])
 return app
