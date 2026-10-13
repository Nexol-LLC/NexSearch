from test_crm import db,pg,users,item,http,header
from uuid import uuid4
from datetime import datetime,timezone,timedelta
from network.auth import Authentication
from network.rates import freshness,margin,comparable
from network.events import EventHub
from psycopg.errors import RaiseException
import pytest

def client(db,person,n=90):return str(db.ingest(item(n),person['employee']['id'])[0])
def order_payload(company):
 return {'company_id':company,'title':'Москва — Владимир','origin':'Москва','destination':'Владимир','vehicle_type':'tent',
         'cargo':[{'name':'Паллеты','L':1200,'W':800,'H':1500,'weight':500,'qty':10}],
         'client_price':80000,'client_vat':'without','carrier_price':60000,'carrier_vat':'without',
         'costs':[{'name':'Расходы','amount':5000,'vat':'without'}]}

def test_pending_registration_needs_director_approval(db):
 a,b=users(db)
 with db.transaction(admin=True) as conn:conn.execute("UPDATE employees SET role='director',is_admin=true WHERE id=%s",(a['employee']['id'],))
 p=Authentication(db).register('Новый','Сотрудник','long-password','new-device','New PC','127.0.0.1')
 assert not p['employee']['approved'];api=http(db)
 assert api.get('/api/me',headers=header(p)).status_code==401
 assert api.get('/api/auth/status',headers=header(p)).json()['employee']['approved'] is False
 ident=str(p['employee']['id']);assert api.patch('/api/admin/employees/'+ident+'/approval',headers=header(b),json={'approved':True}).status_code==403
 assert api.patch('/api/admin/employees/'+ident+'/approval',headers=header(a),json={'approved':True}).status_code==200
 assert api.get('/api/me',headers=header(p)).status_code==200

def test_orders_private_revision_margin_and_linked_plan(db):
 a,b=users(db);company=client(db,a);api=http(db);body=order_payload(company)
 response=api.post('/api/orders',headers=header(a),json=body);assert response.status_code==200,response.text
 order=response.json();assert float(order['margin']['margin_rub'])==15000
 ident=order['id'];assert api.get('/api/orders/'+ident,headers=header(b)).status_code==404
 assert not api.get('/api/orders',headers=header(b)).json()['orders']
 assert api.post('/api/orders',headers=header(b),json=body).status_code==404
 assert api.put('/api/orders/'+ident,headers=header(a),json={**body,'status':'agreed','revision':0}).status_code==200
 assert api.put('/api/orders/'+ident,headers=header(a),json={**body,'revision':0}).status_code==409
 p=api.post('/api/plans',headers=header(a),json={'name':'План','project':{'version':1}}).json()
 assert api.post('/api/orders/'+ident+'/plan',headers=header(a),json={'plan_id':p['id'],'revision':1}).status_code==200
 with db.transaction(admin=True) as conn:conn.execute("UPDATE employees SET role='manager' WHERE id=%s",(b['employee']['id'],))
 assert api.get('/api/plans/'+p['id'],headers=header(b)).status_code==200
 assert api.put('/api/plans/'+p['id'],headers=header(b),json={'name':'Чужое изменение','project':{},'revision':0}).status_code==404

def test_calls_are_append_only_and_create_due_tasks(db):
 a,b=users(db);company=client(db,a);api=http(db);now=datetime.now(timezone.utc)
 for n in range(2):
  r=api.post('/api/calls',headers=header(a),json={'company_id':company,'contact_name':'Контакт','result':'Обсудили маршрут','description':str(n),'called_at':now.isoformat(),'next_contact_at':(now+timedelta(hours=1)).isoformat()});assert r.status_code==200,r.text
 assert len(api.get('/api/calls',headers=header(a)).json()['calls'])==2
 assert not api.get('/api/calls',headers=header(b)).json()['calls']
 tasks=api.get('/api/tasks',headers=header(a)).json()['tasks'];assert len(tasks)==2
 today=api.get('/api/today',headers=header(a)).json();assert len(today['tasks']) in (0,2)
 with pytest.raises(RaiseException):
  with db.transaction(admin=True) as conn:conn.execute("UPDATE call_records SET description='changed'")

def test_standalone_rates_require_original_date_and_keep_basis_and_privacy(db):
 a,b=users(db);api=http(db);now=datetime.now(timezone.utc)
 body={'origin':'Москва','destination':'Владимир','vehicle_type':'tent','source':'VIP калькулятор','amount_rub':150,'vat':'unknown','pricing_basis':'km'}
 assert api.post('/api/routes/quotes',headers=header(a),json=body).status_code==422
 old=(now-timedelta(days=3)).isoformat()
 response=api.post('/api/routes/quotes',headers=header(a),json={**body,'as_of':old});assert response.status_code==200,response.text
 history=api.get('/api/rates/history',headers=header(a)).json()['quotes'];assert len(history)==1
 assert history[0]['freshness']['state']=='stale' and history[0]['pricing_basis']=='km'
 assert not api.get('/api/rates/history',headers=header(b)).json()['quotes']
 assert api.post('/api/routes/quotes',headers=header(a),json={**body,'as_of':(now+timedelta(days=1)).isoformat()}).status_code==400

def test_fresh_rates_keep_source_date_and_do_not_mix_conditions(db):
 now=datetime.now(timezone.utc)
 assert freshness(now-timedelta(hours=25),now=now)['state']=='stale'
 assert freshness(now-timedelta(days=2),now=now)['state']=='stale'
 assert freshness(now-timedelta(hours=1),now-timedelta(minutes=10),now)['state']=='expired'
 assert freshness(None,now=now)['state']=='undated'
 a,b=users(db);api=http(db);company=client(db,a);o=api.post('/api/orders',headers=header(a),json=order_payload(company)).json()
 stale={'amount_rub':50000,'vehicle_type':'tent','vat':'without','as_of':(now-timedelta(days=2)).isoformat(),'source':'Перевозчик'}
 r=api.post('/api/orders/'+o['id']+'/quotes',headers=header(a),json=stale);assert r.status_code==200,r.text
 assert not r.json()['comparable'] and r.json()['freshness']['state']=='stale'
 current={**stale,'as_of':now.isoformat()}
 assert api.post('/api/orders/'+o['id']+'/quotes',headers=header(a),json=current).json()['comparable']
 wrong={**current,'vehicle_type':'lowbed'}
 assert not api.post('/api/orders/'+o['id']+'/quotes',headers=header(a),json=wrong).json()['comparable']
 assert api.get('/api/orders/'+o['id']+'/quotes',headers=header(b)).status_code==404
 assert not margin({**order_payload(company),'client_vat':'included','client_vat_rate':None})['available']

def test_event_stream_scope_has_no_foreign_private_events():
 hub=EventHub();a={'id':'a','role':'logist','department':'main'};b={'id':'b','role':'logist','department':'main'}
 hub.publish('a','call.create','logist','main');hub.publish('b','order.create','logist','main');hub.publish('b','carrier.update','logist','main')
 rows,_=hub.changes(a,0);assert [r['kind'] for r in rows]==['call.create','carrier.update']
 assert all('owner' not in r for r in rows)
 manager={'id':'m','role':'manager','department':'main'};assert len(hub.changes(manager,0)[0])==3
