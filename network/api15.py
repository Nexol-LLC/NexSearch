"""Version 1.5: scheduled searches, exact VAT and authenticated XLSX exports."""
from datetime import datetime,timezone
from decimal import Decimal
from uuid import UUID,uuid4
import io
import xlsxwriter
from fastapi import Depends,Request,HTTPException
from fastapi.responses import Response
from pydantic import Field,field_validator
from psycopg.types.json import Jsonb
from .api_hosted import create_app as base_app
from .api13 import Input,Carrier
from .api14 import FleetVehicle
from .criteria import Criteria
from .vat import calculate

class Schedule(Input):
 run_at:datetime;target:int=Field(ge=1,le=50);criteria:Criteria
 @field_validator('run_at')
 @classmethod
 def future(cls,v):
  if not v.tzinfo or v<=datetime.now(timezone.utc):raise ValueError('Укажите будущее время с часовым поясом')
  return v
class VAT(Input):
 amount:Decimal=Field(ge=0,le=1_000_000_000_000);rate:Decimal=Decimal('22');mode:str=Field(default='add',pattern='^(add|extract|without)$')
class CarrierSave(Carrier):
 id:UUID|None=None;vehicles:list[FleetVehicle]=Field(default_factory=list,max_length=100);regions:str=Field(default='',max_length=3000)
 @field_validator('email')
 @classmethod
 def valid_email(cls,v):
  import re
  if v and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',v.strip()):raise ValueError('Проверьте почту')
  return v.strip()

def create_app(*args,**kwargs):
 app=base_app(*args,**kwargs);db=app.state.db;auth=app.state.auth
 def employee(request:Request):
  token=request.headers.get('authorization','')
  try:
   request.state.user=auth.authenticate(token[7:] if token.startswith('Bearer ') else '',request.client.host);return request.state.user
  except PermissionError as error:raise HTTPException(401,str(error))
 @app.post('/api/vat')
 def vat(body:VAT,user=Depends(employee)):return calculate(body.amount,body.rate,body.mode)
 @app.post('/api/carriers/save')
 def save_carrier(body:CarrierSave,user=Depends(employee)):
  if not body.name.strip():raise ValueError('Укажите наименование')
  with db.transaction(user['id']) as c:
   fields=('name','contacts','routes','description','phone','email','work_terms','vat_mode','vat_rate','regions')
   values=[getattr(body,k).strip() if isinstance(getattr(body,k),str) else getattr(body,k) for k in fields]+[Jsonb([v.model_dump() for v in body.vehicles])]
   if body.id:
    row=c.execute('UPDATE carriers SET '+','.join(k+'=%s' for k in (*fields,'vehicles'))+',updated_by=%s,revision=revision+1,updated_at=now() WHERE id=%s AND revision=%s RETURNING *',(*values,user['id'],body.id,body.revision)).fetchone()
    if not row:raise HTTPException(409,'Карточка изменена другим сотрудником. Откройте её снова.')
   else:
    row=c.execute('INSERT INTO carriers(id,'+','.join((*fields,'vehicles'))+',created_by,updated_by) VALUES('+','.join(['%s']*14)+') RETURNING *',(uuid4(),*values,user['id'],user['id'])).fetchone()
  db.audit(user['id'],'carrier.update' if body.id else 'carrier.create',row['id'],body.model_dump(mode='json'));return row
 @app.get('/api/search/schedules')
 def schedules(user=Depends(employee)):
  with db.transaction(user['id']) as c:return {'schedules':c.execute('SELECT s.*,j.status AS job_status,j.added,j.message FROM search_schedules s LEFT JOIN jobs j ON j.id=s.job_id WHERE s.employee_id=%s ORDER BY s.run_at DESC LIMIT 100',(user['id'],)).fetchall()}
 @app.post('/api/search/schedules')
 def schedule(body:Schedule,user=Depends(employee)):
  with db.transaction(user['id']) as c:
   if c.execute("SELECT count(*) AS n FROM search_schedules WHERE employee_id=%s AND status='pending'",(user['id'],)).fetchone()['n']>=20:raise HTTPException(409,'Можно запланировать до 20 поисков')
   row=c.execute('INSERT INTO search_schedules(id,employee_id,run_at,target,criteria) VALUES(%s,%s,%s,%s,%s) RETURNING *',(uuid4(),user['id'],body.run_at,body.target,Jsonb(body.criteria.model_dump()))).fetchone()
  db.audit(user['id'],'search.schedule',row['id'],body.model_dump(mode='json'));app.state.worker.wake.set();return row
 @app.delete('/api/search/schedules/{ident}')
 def cancel_schedule(ident:UUID,user=Depends(employee)):
  with db.transaction(user['id']) as c:
   row=c.execute("UPDATE search_schedules SET status='cancelled' WHERE id=%s AND employee_id=%s AND status='pending' RETURNING id",(ident,user['id'])).fetchone()
  if not row:raise HTTPException(409,'Поиск уже запущен или отменён')
  db.audit(user['id'],'search.schedule_cancel',ident);return {'ok':True}
 # Specific route inserted ahead of the legacy /api/export/{kind} matcher.
 @app.get('/api/export/orders')
 def orders_export(employee_id:UUID|None=None,status:str='',q:str='',user=Depends(employee)):
  owner=employee_id or user['id']
  with db.transaction(user['id']) as c:
   if not c.execute('SELECT nexsearch_can_read(%s) AS allowed',(owner,)).fetchone()['allowed']:raise HTTPException(403,'База сотрудника недоступна')
   items=c.execute("SELECT o.*,c.name AS company_name,c.inn,ca.name AS carrier_name FROM shipment_orders o JOIN companies c ON c.id=o.company_id LEFT JOIN carriers ca ON ca.id=o.carrier_id WHERE o.employee_id=%s AND (%s='' OR o.status=%s) ORDER BY o.number",(owner,status,status)).fetchall()
  items=[o for o in items if q.casefold() in ' '.join(str(o.get(k) or '') for k in ('number','title','company_name','origin','destination')).casefold()]
  from .api14 import STATES,VEHICLES
  vat_terms={'unknown':'Не уточнён','included':'НДС включён','without':'Без НДС'}
  for o in items:
   o['client_vat']=vat_terms.get(o['client_vat'],o['client_vat']);o['carrier_vat']=vat_terms.get(o['carrier_vat'],o['carrier_vat'])
   o['costs']=[dict(v,vat=vat_terms.get(v.get('vat'),'Не уточнён')) for v in o['costs']]
  stream=io.BytesIO();book=xlsxwriter.Workbook(stream,{'in_memory':True,'strings_to_formulas':False,'strings_to_urls':False})
  head=book.add_format({'bold':True,'bg_color':'#EDE9FC','text_wrap':True,'valign':'vcenter','align':'center'});money=book.add_format({'num_format':'#,##0.00'});date=book.add_format({'num_format':'dd.mm.yyyy hh:mm'})
  def sheet(name,headers,rows):
   ws=book.add_worksheet(name);ws.hide_gridlines(2);ws.freeze_panes(1,1);ws.write_row(0,0,headers,head);ws.set_row(0,32);ws.set_column(0,len(headers)-1,24)
   for n,row in enumerate(rows,1):
    for col,value in enumerate(row):
     if isinstance(value,Decimal):ws.write_number(n,col,float(value),money)
     elif isinstance(value,datetime):ws.write_datetime(n,col,value.astimezone(__import__('zoneinfo').ZoneInfo('Europe/Moscow')).replace(tzinfo=None),date)
     elif value is not None:ws.write(n,col,value)
   ws.autofilter(0,0,max(1,len(rows)),len(headers)-1)
  sheet('Заявки и рейсы',['Номер','Название','Компания','ИНН','Статус','Откуда','Куда','Погрузка (МСК)','Выгрузка (МСК)','Автомобиль','Перевозчик','Цена клиенту, ₽','НДС клиента','НДС клиента, %','Цена перевозчика, ₽','НДС перевозчика','НДС перевозчика, %','Примечание'],[[o['number'],o['title'],o['company_name'],o['inn'],STATES.get(o['status'],o['status']),o['origin'],o['destination'],o['pickup_at'],o['delivery_at'],VEHICLES.get(o['vehicle_type'],''),o['carrier_name'],o['client_price'],o['client_vat'],o['client_vat_rate'],o['carrier_price'],o['carrier_vat'],o['carrier_vat_rate'],o['notes']] for o in items])
  sheet('Грузы',['Заявка №','Наименование','Длина, мм','Ширина, мм','Высота, мм','Масса места, кг','Количество','Выгрузка №'],[[o['number'],*[v.get(k) for k in ('name','L','W','H','weight','qty','stop')]] for o in items for v in o['cargo']])
  sheet('Расходы',['Заявка №','Название','Сумма, ₽','НДС','Ставка НДС, %'],[[o['number'],*[v.get(k) for k in ('name','amount','vat','vat_rate')]] for o in items for v in o['costs']])
  book.close();db.audit(user['id'],'data.export','orders',{'rows':len(items),'owner':str(owner)})
  return Response(stream.getvalue(),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename=NexSearch-orders.xlsx'})
 route=app.router.routes.pop();index=next(i for i,r in enumerate(app.router.routes) if getattr(r,'path','')=='/api/export/{kind}');app.router.routes.insert(index,route)
 return app
