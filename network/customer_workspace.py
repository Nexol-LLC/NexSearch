"""Company timelines and atomic, role-scoped reassignment of active clients."""
from uuid import UUID
from fastapi import Depends,HTTPException
from pydantic import Field
from psycopg import sql
from psycopg.types.json import Jsonb
from .api13 import Input
from .database import NotFound,Conflict
from .presentation import summary
from .notifications import notify

class Transfer(Input):
    employee_id:UUID
    revision:int=Field(ge=0)
    note:str=Field(default='',max_length=3000)
class Interaction(Input):description:str=Field(min_length=1,max_length=10000)

def install(app,db,employee):
    def company(user,ident):
        with db.transaction(user['id']) as c:
            row=c.execute('''SELECT co.id,co.payload,l.employee_id,l.status AS lead_status,l.called,l.call_note,l.called_at,l.revision,
              cl.stage,cl.revision AS stage_revision,cl.origin,cl.destination,cl.cargo,cl.weight_kg,COALESCE(cl.active,false) AS is_client,
              e.first_name AS owner_first_name,e.last_name AS owner_last_name
              FROM companies co JOIN employee_leads l ON l.company_id=co.id JOIN employees e ON e.id=l.employee_id
              LEFT JOIN employee_clients cl ON cl.company_id=co.id WHERE co.id=%s''',(ident,)).fetchone()
        if not row:raise NotFound('Компания недоступна: проверьте текущего ответственного')
        return {**summary(row.pop('payload')),**row}

    def may_manage(user,person):
        return user['role']=='director' or user['role']=='manager' and (str(person['id'])==str(user['id']) or person['role']=='logist' and person['department']==user['department'])

    @app.get('/api/companies/{ident}')
    def get_company(ident:UUID,user=Depends(employee)):return company(user,ident)

    @app.get('/api/companies/{ident}/transfer-targets')
    def targets(ident:UUID,user=Depends(employee)):
        current=company(user,ident)
        if user['role'] not in ('manager','director'):raise HTTPException(403,'Передача доступна руководителю отдела и директору')
        if not current['is_client']:raise Conflict('Передавать можно клиентов, взятых в работу')
        with db.transaction(user['id']) as c:
            people=c.execute("SELECT id,first_name,last_name,role,department FROM employees WHERE active AND approved AND login<>'host' ORDER BY last_name,first_name").fetchall()
        return {'employees':[p for p in people if may_manage(user,p) and str(p['id'])!=str(current['employee_id'])]}

    @app.post('/api/companies/{ident}/transfer')
    def transfer(ident:UUID,body:Transfer,user=Depends(employee)):
        if user['role'] not in ('manager','director'):raise HTTPException(403,'Передача доступна руководителю отдела и директору')
        # Authorization, ownership change, tasks, contacts and audit share one commit.
        with db.transaction(admin=True) as c:
            lead=c.execute('SELECT * FROM employee_leads WHERE company_id=%s FOR UPDATE',(ident,)).fetchone()
            if not lead:raise NotFound('Клиент недоступен')
            source=c.execute('SELECT id,first_name,last_name,role,department FROM employees WHERE id=%s',(lead['employee_id'],)).fetchone()
            if not may_manage(user,source):raise NotFound('Клиент находится вне вашего отдела')
            target=c.execute("SELECT id,first_name,last_name,role,department FROM employees WHERE id=%s AND active AND approved AND login<>'host' FOR SHARE",(body.employee_id,)).fetchone()
            if not target or not may_manage(user,target):raise NotFound('Получатель недоступен в пределах вашей должности')
            if str(source['id'])==str(target['id']):raise Conflict('Этот сотрудник уже отвечает за клиента')
            if lead['revision']!=body.revision:raise Conflict('Карточка изменилась. Откройте её заново перед передачей')
            client=c.execute('SELECT * FROM employee_clients WHERE company_id=%s AND active FOR UPDATE',(ident,)).fetchone()
            if not client or lead['status']!='taken':raise Conflict('Передавать можно клиентов, взятых в работу')
            c.execute('DELETE FROM employee_clients WHERE company_id=%s',(ident,))
            c.execute('UPDATE employee_leads SET employee_id=%s,revision=revision+1,updated_at=now() WHERE company_id=%s',(target['id'],ident))
            client.update(employee_id=target['id'],revision=client['revision']+1)
            c.execute(sql.SQL('INSERT INTO employee_clients({}) VALUES({})').format(sql.SQL(',').join(map(sql.Identifier,client)),sql.SQL(',').join(sql.Placeholder() for _ in client)),tuple(client.values()))
            tasks=c.execute("UPDATE followup_tasks SET employee_id=%s,assigned_by=%s,revision=revision+1 WHERE company_id=%s AND status='open' RETURNING id",(target['id'],user['id'],ident)).fetchall()
            c.execute('UPDATE company_contacts SET employee_id=%s WHERE company_id=%s',(target['id'],ident))
            if tasks:c.execute("UPDATE notifications SET read_at=COALESCE(read_at,now()) WHERE employee_id=%s AND kind LIKE 'task.%%' AND entity_id=ANY(%s)",(source['id'],[str(t['id']) for t in tasks]))
            details={'from_employee_id':str(source['id']),'to_employee_id':str(target['id']),'from_name':source['first_name']+' '+source['last_name'],'to_name':target['first_name']+' '+target['last_name'],'description':body.note,'open_tasks':len(tasks)}
            c.execute("INSERT INTO audit_events(employee_id,kind,entity_id,details) VALUES(%s,'company.transfer',%s,%s)",(user['id'],str(ident),Jsonb(details)))
            name=c.execute('SELECT name FROM companies WHERE id=%s',(ident,)).fetchone()['name']
            for owner,title,text in [(target['id'],'Вам передан клиент',name+' · от '+details['from_name']),(source['id'],'Клиент передан',name+' · новый ответственный: '+details['to_name'])]:
                notify(c,owner,'client.transferred',title,text,ident)
        for person in (source,target):db.events.publish(person['id'],'company.transfer',person['role'],person['department'])
        return {'transferred':True,'employee_id':target['id'],'open_tasks':len(tasks)}

    @app.post('/api/companies/{ident}/interactions')
    def add_interaction(ident:UUID,body:Interaction,user=Depends(employee)):
        with db.transaction(admin=True) as c:
            owner=c.execute('SELECT e.id,e.role,e.department FROM employee_leads l JOIN employees e ON e.id=l.employee_id WHERE l.company_id=%s FOR SHARE OF l',(ident,)).fetchone()
            if not owner or not (str(owner['id'])==str(user['id']) or may_manage(user,owner)):raise NotFound('Компания недоступна')
            c.execute("INSERT INTO audit_events(employee_id,kind,entity_id,details) VALUES(%s,'company.note',%s,%s)",(user['id'],str(ident),Jsonb({'description':body.description})))
        db.events.publish(user['id'],'company.note',user['role'],user['department']);return {'saved':True}

    @app.get('/api/companies/{ident}/interactions')
    def interactions(ident:UUID,user=Depends(employee)):
        # Current client access grants its shared CRM history, never private email.
        with db.transaction(admin=True) as c:
            owner=c.execute('SELECT e.id,e.role,e.department FROM employee_leads l JOIN employees e ON e.id=l.employee_id WHERE l.company_id=%s FOR SHARE OF l',(ident,)).fetchone()
            if not owner or not (str(owner['id'])==str(user['id']) or may_manage(user,owner)):raise NotFound('История компании недоступна')
            calls=c.execute('SELECT r.*,e.first_name,e.last_name FROM call_records r JOIN employees e ON e.id=r.employee_id WHERE r.company_id=%s ORDER BY r.called_at DESC LIMIT 500',(ident,)).fetchall()
            events=c.execute('''SELECT a.*,e.first_name,e.last_name FROM audit_events a LEFT JOIN employees e ON e.id=a.employee_id
              WHERE (a.entity_id=%s AND (a.kind LIKE 'lead.%%' OR a.kind IN ('client.stage','company.contacts','company.transfer','company.note')))
              OR (a.details->>'company_id'=%s AND a.kind IN ('task.create','task.update','contact.create'))
              ORDER BY a.created_at DESC,a.id DESC LIMIT 500''',(str(ident),str(ident))).fetchall()
            tasks=c.execute('''SELECT t.*,e.first_name,e.last_name FROM followup_tasks t JOIN employees e ON e.id=t.employee_id
              WHERE t.company_id=%s AND NOT EXISTS(SELECT 1 FROM audit_events a WHERE a.kind='task.create' AND a.entity_id=t.id::text)
              ORDER BY t.created_at DESC LIMIT 500''',(ident,)).fetchall()
        items=[]
        for r in calls:items.append({'id':'call:'+str(r['id']),'kind':'call','at':r['called_at'],'title':r['result'],'description':r['description'],'actor':r['first_name']+' '+r['last_name'],'contact':r['contact_name'],'phone':r['phone']})
        labels={'client.stage':'Изменён этап','company.contacts':'Обновлены рабочие контакты','company.transfer':'Передан клиент','company.note':'Заметка','task.create':'Создана задача','task.update':'Изменена задача','contact.create':'Добавлено контактное лицо'}
        for r in events:
            d=r['details'];kind=r['kind'];description=d.get('description','');title=labels.get(kind,'Изменён статус компании')
            if kind=='client.stage':description=str(d.get('before',''))+' → '+str(d.get('after',''))
            elif kind=='company.transfer':description=d.get('from_name','')+' → '+d.get('to_name','')+('\n'+description if description else '')
            elif kind.startswith('task.'):description=d.get('title','')+(' · '+d['status'] if d.get('status') else '')+('\n'+description if description else '')
            elif kind=='company.contacts':description='Телефоны: '+', '.join(d.get('after',{}).get('phones',[]))+'\nПочта: '+', '.join(d.get('after',{}).get('emails',[]))
            elif kind.startswith('lead.'):description={'taken':'Взят в работу','refused':'Отказ','new':'Новый','processing':'В обработке'}.get(kind.split('.')[-1],kind)
            elif kind=='contact.create':description=d.get('name','')+' · '+d.get('phones','')+' · '+d.get('email','')
            items.append({'id':'audit:'+str(r['id']),'kind':kind,'at':r['created_at'],'title':title,'description':description,'actor':((r['first_name'] or '')+' '+(r['last_name'] or '')).strip() or 'Система'})
        for t in tasks:items.append({'id':'task:'+str(t['id']),'kind':'task.create','at':t['created_at'],'title':'Создана задача','description':t['title']+'\n'+t['description'],'actor':t['first_name']+' '+t['last_name']})
        items.sort(key=lambda i:(i['at'],i['id']),reverse=True)
        return {'interactions':items[:500],'limit':500}
