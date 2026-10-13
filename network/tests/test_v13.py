from test_crm import db,pg,users,item,http,header,ROOT
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from network.auth import Authentication
from network.criteria import Criteria,matches,SEGMENTS
from network.checko import normalize,Checko
from psycopg.errors import RaiseException
import pytest

def elevate(db,person,role):
 with db.transaction(admin=True) as conn:conn.execute('UPDATE employees SET approved=true,role=%s,is_admin=%s WHERE id=%s',(role,role=='director',person['employee']['id']))

def test_positions_private_clients_and_common_carriers(db):
 a,b=users(db);auth=Authentication(db)
 m=auth.register('Руководитель','Отдела','password-manager','dm','PC M','127.0.0.1',requested_role='manager')
 d=auth.register('Директор','Компании','password-director','dd','PC D','127.0.0.1',requested_role='director')
 assert m['employee']['role']=='logist' and d['employee']['role']=='logist'
 elevate(db,m,'manager');elevate(db,d,'director');api=http(db)
 ids=[]
 for i,u in enumerate([a,b,m,d]):
  ident,_,_=db.ingest(item(i+20),u['employee']['id']);ids.append(str(ident))
  assert api.patch('/api/leads/'+str(ident),headers=header(u),json={'status':'taken','called':True,'call_note':'Тест','revision':0}).status_code==200
 assert api.get('/api/admin/employees',headers=header(a)).status_code==403
 assert len(api.get('/api/clients',headers=header(a)).json()['companies'])==1
 team=api.get('/api/admin/employees',headers=header(m)).json()['employees'];assert len(team)==3
 assert api.get('/api/admin/employees/'+str(a['employee']['id'])+'/clients',headers=header(m)).json()['companies'][0]['id']==ids[0]
 assert api.get('/api/admin/employees/'+str(d['employee']['id'])+'/clients',headers=header(m)).status_code==404
 assert api.patch('/api/clients/'+ids[0]+'/stage',headers=header(m),json={'stage':'Попытка','revision':0}).status_code==404
 assert len(api.get('/api/admin/employees',headers=header(d)).json()['employees'])==4
 assert api.patch('/api/admin/employees/'+str(b['employee']['id'])+'/role',headers=header(m),json={'role':'director'}).status_code==403
 assert api.post('/api/carriers',headers=header(a),json={'name':'Общий перевозчик','routes':'Москва → Владимир'}).status_code==200
 assert len(api.get('/api/carriers',headers=header(b)).json()['carriers'])==1
 assert api.get('/api/history',headers=header(a)).status_code==403
 events=api.get('/api/history',headers=header(d)).json()['events'];assert events and all(e['event_hash'] for e in events)

def test_revenue_segments_boundaries_and_sql_claims(db):
 a,b=users(db);segments=list(SEGMENTS)
 for i,amount in enumerate([9_999_999,10_000_000,99_999_999,100_000_000,499_999_999,500_000_000,999_999_999,1_000_000_000]):
  v=item(i+50);v['financial']['revenue_rub']=amount;v['financial']['reports']['2025']['2110']=amount;db.ingest(v)
  expected=None if i==0 else segments[0] if i<3 else segments[1] if i<5 else segments[2] if i<7 else segments[3]
  assert [s for s in segments if matches(v,Criteria(revenue_segments=[s]))]==([] if expected is None else [expected])
 selected=db.claim_available(a['employee']['id'],10,criteria={'revenue_segments':[segments[1]],'sectors':['43']})
 assert len(selected)==2
 assert not db.claim_available(b['employee']['id'],10,criteria={'revenue_segments':[segments[1]],'sectors':['43']})
 assert not matches(item(3),Criteria(report_year=2024,revenue_segments=[segments[0]]))

def test_authoritative_finances_units_and_missing_tax(db):
 base={'ИНН':'5402176081','Статус':{'Код':'001','Наим':'Действует'},'УстКап':{'Сумма':10000},'Налоги':{'СведУплГод':2024,'СведУпл':[{'Наим':'Транспортный налог','Сумма':12500}]}}
 company={'data':base};finance={'data':{'2024':{'2110':123456789,'2400':7654321}},'bo.nalog.ru':{'Отчет':{'2024':'https://bo.nalog.ru/report.pdf'}}}
 result=normalize(company,finance,base['ИНН']);assert result['financial']['revenue_rub']==123456789 and result['financial']['transport_tax_rub']==12500
 base['Налоги']={};result=normalize(company,{},base['ИНН']);assert result['financial']['transport_tax_rub'] is None and result['financial']['revenue_rub'] is None
 base['Статус']['Код']='201';assert not normalize(company,{},base['ИНН'])['verification']['active']
 checker=Checko(db);calls=[]
 checker.request=lambda method,inn:(calls.append(method) or (company if method=='company' else finance))
 checker.verify(base['ИНН']);checker.verify(base['ИНН']);assert calls==['company']

def test_invalid_and_inactive_not_claimed_and_audit_immutable(db):
 a,b=users(db)
 invalid=item(60);invalid['inn']='';db.ingest(invalid)
 inactive=item(61);inactive['verification']['active']=False;db.ingest(inactive)
 assert not db.claim_available(a['employee']['id'],10)
 with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda n:db.audit(a['employee']['id'],'test.concurrent',str(n)),range(20)))
 with db.transaction(admin=True) as conn:
  rows=conn.execute('SELECT * FROM audit_events ORDER BY id').fetchall()
  assert all(rows[i]['previous_hash']==rows[i-1]['event_hash'] for i in range(1,len(rows)))
 with pytest.raises(RaiseException):
  with db.transaction(admin=True) as conn:conn.execute("UPDATE audit_events SET kind='tampered'")

def test_worker_director_owns_results_and_all_mode_claims_beyond_one_hundred(db):
 from network.search import SearchWorker
 a,b=users(db);elevate(db,a,'director')
 for n in range(215):db.ingest(item(n+300))
 checker=Checko(db);checker.key=lambda:'configured-for-test'
 worker=SearchWorker(db,checker);worker.discover=lambda *args:iter(())
 def job(user,target):
  with db.transaction(admin=True) as conn:
   row=conn.execute("INSERT INTO jobs(id,employee_id,target,status) VALUES(%s,%s,%s,'running') RETURNING *",(uuid4(),user['employee']['id'],target)).fetchone()
  worker.run(row);return worker.job(row['id'])
 first=job(a,10);assert first['added']==10 and first['status']=='completed'
 assert len(db.leads(a['employee']['id']))==10
 all_new=job(b,None);assert all_new['added']==205 and all_new['status']=='completed'
 assert len(db.leads(b['employee']['id']))==205
 assert not set(str(r['id']) for r in db.leads(a['employee']['id']))&set(str(r['id']) for r in db.leads(b['employee']['id']))
