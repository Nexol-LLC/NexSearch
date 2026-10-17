"""Isolated loopback 1.6 preview; synthetic companies and cached mail only."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
from uuid import uuid4
import json,os,secrets,sys,threading,time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
HOME=ROOT/'network/.runtime/preview16'
assert HOME.resolve().is_relative_to((ROOT/'network/.runtime').resolve())
HOME.mkdir(parents=True,exist_ok=True)
os.environ['NEXOL_HOME']=str(HOME)
os.environ['NEXSEARCH_UPDATE_STATE']=str(HOME/'updates.json')
from network.api16 import create_app
from network.auth import Authentication
from network.database import Database
from network.postgres_runtime import start_postgres,stop_postgres
from network.tls import identity
from network.client import start_client
from network.mailbox import seal
from network.notifications import notify
from psycopg.types.json import Jsonb
import uvicorn

def main():
    runtime=ROOT/'preview/HostWindows/postgres'
    trust,cert,key=identity(HOME/'identity')
    os.environ['NEXSEARCH_KEY_FILE']=str(key);os.environ['NEXOL_HOST_ID']=trust['host_id']
    db=Database(start_postgres(HOME,runtime,54368));db.initialize()
    # This fixture owns only its dedicated loopback cluster, never the user's DB.
    with db.transaction(admin=True) as c:c.execute('TRUNCATE employees,companies,jobs,carriers,audit_events,company_checks CASCADE')
    app=create_app(db,web=ROOT/'network/web',pack=ROOT/'pack',start_worker=False)
    auth=Authentication(db);people={}
    for name,first,last,role in [('employee','Анна','Петрова','logist'),('colleague','Иван','Орлов','logist'),('manager','Алексей','Волков','manager'),('director','Елена','Смирнова','director')]:
        result=auth.register(first,last,secrets.token_urlsafe(24),'preview16-'+name,'Тестовый компьютер','127.0.0.1',login='preview.'+name)
        with db.transaction(admin=True) as c:c.execute('UPDATE employees SET role=%s,approved=true WHERE id=%s',(role,result['employee']['id']))
        result['employee'].update(role=role,approved=True);people[name]=result
    def inn(n):
        digits='7700'+str(n).zfill(5);return digits+str(sum(int(d)*w for d,w in zip(digits,[2,4,10,3,5,9,4,6,8]))%11%10)
    catalogue={}
    names=['ДЕМО Металл','ДЕМО Техника','ДЕМО Продукты','ДЕМО Кабель','ДЕМО Мебель','ДЕМО Машины']
    owner=people['employee']['employee']['id'];company_ids=[]
    for i,name in enumerate(names,1):
        item={'inn':inn(i),'name':'ООО «'+name+'»','ogrn':'1000000000000','kpp':'770001001',
              'region':['Москва','Самарская область','Свердловская область'][i%3],'segment':'Производство оборудования',
              'legal_address':'Учебный адрес','phones':['+7 000 000-00-01'],'emails':['logistics'+str(i)+'@example.org'],
              'source':'Учебный каталог','source_url':'https://example.org/company/'+str(i),'notes':[],'contact_sources':[],
              'verification':{'active':True,'okved':'28.99','checked_at':datetime.now(timezone.utc).isoformat(),'source_url':'https://example.org/check/'+str(i)},
              'financial':{'year':2025,'revenue_rub':125000000,'capital_rub':100000,'profit_rub':13000000,'message':'Учебные сведения'}}
        catalogue[item['inn']]=item
        if i<=5:
            ident,_,_=db.ingest(item,owner);company_ids.append(ident)
            if i<=3:db.update_lead(owner,ident,'taken',True,'Обсудили перевозку. Следующий шаг — уточнить маршрут.',0)
    def verify(number):
        from network.checko import VerificationUnavailable
        if number not in catalogue:raise VerificationUnavailable('В учебном запуске доступны только тестовые ИНН')
        return dict(catalogue[number])
    app.state.checker.verify=verify
    account=uuid4();message=uuid4()
    with db.transaction(owner) as c:
        c.execute('''INSERT INTO carriers(id,name,phone,email,routes,description,vehicles,created_by,updated_by)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(uuid4(),'ДЕМО Перевозчик','+7 000 000-00-02','carrier@example.org','Москва — Самара','Учебный перевозчик',Jsonb([dict(name='Тент 20 т',kind='tent',location='Москва',weight_kg=14000,payload_kg=20000,volume_m3=82,length_mm=13600,width_mm=2450,height_mm=2450,notes='')]),owner,owner))
        task_id=uuid4()
        c.execute('''INSERT INTO followup_tasks(id,employee_id,assigned_by,company_id,title,description,due_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s)''',(task_id,owner,people['manager']['employee']['id'],company_ids[0],'Уточнить график отгрузок','Связаться с логистом компании',datetime.now(timezone.utc)+timedelta(minutes=5)))
        c.execute('''INSERT INTO mail_accounts(id,employee_id,email,login,label,imap_host,smtp_host,secret,folders,last_sync_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,now())''',(account,owner,'demo@example.org','demo@example.org','Учебная переписка','imap.example.org','smtp.example.org',seal('preview-only',owner,account),Jsonb([{'id':'INBOX','name':'Входящие','sent':False}])) )
        c.execute('''INSERT INTO mail_messages(id,employee_id,account_id,folder,uidvalidity,uid,subject,sender,recipients,addresses,sent_at,seen,body_text)
          VALUES(%s,%s,%s,'INBOX','1',1,%s,%s,%s,%s,now(),true,%s)''',(message,owner,account,'Запрос ставки на перевозку','Логистика <logistics1@example.org>',Jsonb(['demo@example.org']),['logistics1@example.org','demo@example.org'],'Добрый день!\n\nПросим рассчитать перевозку оборудования по маршруту Самара — Москва. Масса 12 т, погрузка в понедельник.\n\nЭто учебное письмо для проверки интерфейса NexSearch 1.6.0.'))
        notify(c,owner,'mail.new','Запрос ставки на перевозку','Учебное письмо',message,'preview-mail')
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=9037,ssl_certfile=str(cert),ssl_keyfile=str(key),log_config=None,access_log=False))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    while not server.started:
        if not thread.is_alive():raise RuntimeError('Preview host did not start')
        time.sleep(.1)
    clients=[]
    for name,port in [('employee',9038),('manager',9040),('director',9042)]:
        profile={'host_url':'https://127.0.0.1:9037','computer_name':'Учебная база 1.6','token':people[name]['token'],'profile':people[name]['employee']}
        bridge,agent,close=start_client(trust,HOME/('client-'+name),ROOT/'network/web',port=port,open_browser=False,profile=profile,discovery=False)
        threading.Thread(target=bridge.serve_forever,daemon=True).start();clients.append(close)
    (HOME/'ready.json').write_text(json.dumps({'employee':'http://127.0.0.1:9038','manager':'http://127.0.0.1:9040','director':'http://127.0.0.1:9042','inn':inn(6),'company_id':str(company_ids[0])}),encoding='utf-8')
    stop=HOME/'stop';stop.unlink(missing_ok=True)
    print('NexSearch 1.6.0 demo ready: employee 9038, manager 9040, director 9042. Synthetic data only.',flush=True)
    try:
        while not stop.exists():time.sleep(.5)
    finally:
        for close in clients:close()
        server.should_exit=True;thread.join(timeout=15);db.close();stop_postgres(HOME,runtime)

if __name__=='__main__':main()
