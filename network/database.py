from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

class Conflict(ValueError):pass
class NotFound(ValueError):pass

class Database:
    def __init__(self,dsn):
        from .events import EventHub
        self.events=EventHub()
        self.pool=ConnectionPool(dsn,min_size=1,max_size=12,kwargs={'row_factory':dict_row},open=True)
        self.pool.wait(timeout=20)

    @contextmanager
    def transaction(self,employee=None,admin=False,role=None,department='main'):
        with self.pool.connection() as db:
            with db.transaction():
                if employee and role is None:
                    person=db.execute('SELECT role,department,is_admin FROM employees WHERE id=%s',(employee,)).fetchone()
                    if person:role='director' if person['is_admin'] else person['role'];department=person['department']
                db.execute("SELECT set_config('nexol.employee_id',%s,true),set_config('nexol.is_admin',%s,true),set_config('nexol.role',%s,true),set_config('nexol.department',%s,true)",
                    (str(employee or ''),'true' if admin else 'false',role or '',department))
                yield db

    def initialize(self):
        with self.transaction(admin=True) as db:
            ready=db.execute("SELECT to_regclass('public.schema_meta') AS ready").fetchone()['ready']
            if ready and db.execute('SELECT 1 FROM schema_meta WHERE version=14').fetchone():return
            db.execute(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
            db.execute(Path(__file__).with_name('schema14.sql').read_text(encoding='utf-8'))

    def audit(self,employee,kind,entity='',details=None,ip=''):
        import json
        with self.transaction(employee,admin=employee is None) as db:
            ident=db.execute('INSERT INTO audit_events(employee_id,kind,entity_id,details,ip) VALUES(%s,%s,%s,%s,%s) RETURNING id',
                (employee,kind,str(entity),Jsonb(json.loads(json.dumps(details or {},default=str))),ip)).fetchone()['id']
            person=db.execute('SELECT role,department FROM employees WHERE id=%s',(employee,)).fetchone() if employee else None
        self.events.publish(employee,kind,person['role'] if person else '',person['department'] if person else 'main')
        return ident

    def ingest(self,item,employee=None,processing=False,job=None):
        """A global company identity and a permanent, unique employee reservation."""
        from collector import identities,signatures,valid_inn,unique_phones
        keys=identities(item);weak=signatures(item)
        if not keys and not weak:raise ValueError('Нет идентификаторов компании')
        inn=item.get('inn') if valid_inn(item.get('inn')) else None
        with self.transaction(admin=True) as db:
            # All dedup keys are checked in one short transaction, before assignment.
            db.execute('SELECT pg_advisory_xact_lock(73142001)')
            matches=db.execute('SELECT DISTINCT c.* FROM companies c JOIN company_identities i ON i.company_id=c.id WHERE i.identity=ANY(%s)',(keys,)).fetchall() if keys else []
            if not matches and weak:
                matches=db.execute('SELECT DISTINCT c.* FROM companies c JOIN company_signatures s ON s.company_id=c.id WHERE s.signature=ANY(%s) AND (%s::text IS NULL OR c.inn IS NULL OR c.inn=%s)',(weak,inn,inn)).fetchall()
                if len(matches)>1:matches=[]
            is_new=not matches
            if matches:
                old=matches[0];ident=old['id'];payload=old['payload']
                for key,value in item.items():
                    if key in ('phones','emails','notes','contact_sources','categories'):
                        payload[key]=list(dict.fromkeys(payload.get(key,[])+(value or [])))
                    elif key=='extra':payload.setdefault('extra',{}).update(value or {})
                    elif key in ('verification','financial','checko_company','status','status_source','segment'):payload[key]=value
                    elif not payload.get(key) and value:payload[key]=value
                payload['phones']=unique_phones(payload.get('phones',[]))
                if item.get('status_source'):
                    payload['status']=item['status'];payload['status_source']=item['status_source']
                payload.setdefault('sources',[])
                provenance={'name':item.get('source',''),'url':item.get('source_url','')}
                if provenance not in payload['sources']:payload['sources'].append(provenance)
                db.execute('UPDATE companies SET inn=COALESCE(inn,%s),payload=%s,updated_at=now() WHERE id=%s',(inn,Jsonb(payload),ident))
            else:
                ident=uuid4();payload=dict(item);payload['sources']=[{'name':item.get('source',''),'url':item.get('source_url','')}]
                db.execute('INSERT INTO companies(id,name,inn,payload) VALUES(%s,%s,%s,%s)',(ident,item['name'],inn,Jsonb(payload)))
            for key in keys:db.execute('INSERT INTO company_identities VALUES(%s,%s) ON CONFLICT DO NOTHING',(key,ident))
            for key in weak:db.execute('INSERT INTO company_signatures VALUES(%s,%s) ON CONFLICT DO NOTHING',(key,ident))
            if item.get('verification'):
                db.execute('UPDATE companies SET active_verified=%s,verified_at=%s WHERE id=%s',
                    (bool(item['verification'].get('active')),item['verification'].get('checked_at') or __import__('datetime').datetime.now(__import__('datetime').timezone.utc),ident))
            assigned=False
            allowed=True
            if job:
                current=db.execute('SELECT target,added FROM jobs WHERE id=%s FOR UPDATE',(job,)).fetchone()
                allowed=current['target'] is None or current['added']<current['target']
            if employee and allowed and item.get('verification',{}).get('active') and inn and len(inn)==10:
                assigned=bool(db.execute("INSERT INTO employee_leads(company_id,employee_id,status,job_id) VALUES(%s,%s,%s,%s) ON CONFLICT(company_id) DO NOTHING RETURNING company_id",(ident,employee,'processing' if processing else 'new',job)).fetchone())
            if job and allowed and (assigned if employee else is_new):db.execute('UPDATE jobs SET added=added+1 WHERE id=%s',(job,))
            return ident,is_new,assigned

    def claim_available(self,employee,limit,job=None,criteria=None):
        from .criteria import matches,sql_filter
        filters,args=sql_filter(criteria)
        with self.transaction(admin=True) as db:
            rows=db.execute("SELECT c.id,c.payload FROM companies c WHERE c.state='ready' AND c.active_verified AND c.inn IS NOT NULL AND c.verified_at>now()-interval '24 hours' AND NOT EXISTS(SELECT 1 FROM employee_leads l WHERE l.company_id=c.id)"+filters+" ORDER BY c.created_at LIMIT %s FOR UPDATE OF c SKIP LOCKED",(*args,limit)).fetchall()
            claimed=[]
            for row in rows:
                if len(claimed)>=limit:break
                if not matches(row['payload'],criteria):continue
                result=db.execute('INSERT INTO employee_leads(company_id,employee_id,job_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING RETURNING company_id',(row['id'],employee,job)).fetchone()
                if result:claimed.append(str(row['id']))
            if job and claimed:db.execute('UPDATE jobs SET added=added+%s WHERE id=%s',(len(claimed),job))
            return claimed

    def leads(self,employee,clients=False,stage=None,order='recent',actor=None):
        with self.transaction(actor or employee) as db:
            if clients:
                sorting={'recent':'cl.created_at DESC','stage':'lower(cl.stage),c.name','name':'c.name'}[order]
                rows=db.execute(f'''SELECT c.id,c.payload,l.status AS lead_status,l.called,l.call_note,l.called_at,l.revision,
                    cl.stage,cl.revision AS stage_revision,cl.origin,cl.destination,cl.cargo,cl.weight_kg,l.employee_id FROM employee_clients cl JOIN employee_leads l ON l.company_id=cl.company_id
                    JOIN companies c ON c.id=cl.company_id WHERE cl.employee_id=%s AND cl.active AND (%s::text IS NULL OR cl.stage=%s) ORDER BY {sorting}''',(employee,stage,stage)).fetchall()
            else:
                rows=db.execute('''SELECT c.id,c.payload,l.status AS lead_status,l.called,l.call_note,l.called_at,l.revision,l.employee_id
                    FROM employee_leads l JOIN companies c ON c.id=l.company_id WHERE l.employee_id=%s ORDER BY l.created_at DESC''',(employee,)).fetchall()
            from .presentation import summary
            return [dict(summary(r.pop('payload')),**r) for r in rows]

    def update_lead(self,employee,company_id,status,called,note,revision):
        with self.transaction(employee) as db:
            old=db.execute('SELECT * FROM employee_leads WHERE company_id=%s AND employee_id=%s FOR UPDATE',(company_id,employee)).fetchone()
            if not old:raise NotFound('Компания не найдена в вашей базе')
            if old['revision']!=revision:raise Conflict('Запись изменена. Обновите карточку; ваш текст сохранён в поле.')
            if status=='taken' and not called:raise ValueError('Чтобы взять клиента в работу, отметьте звонок')
            if called and (not old['called'] or note!=old['call_note']):
                db.execute('INSERT INTO call_records(id,employee_id,company_id,result,description,called_at) VALUES(%s,%s,%s,%s,%s,now())',(uuid4(),employee,company_id,{'taken':'Взят в работу','refused':'Отказ','new':'Звонок','processing':'В обработке'}[status],note))
            result=db.execute('''UPDATE employee_leads SET status=%s,called=%s,call_note=%s,
                called_at=CASE WHEN %s THEN COALESCE(called_at,now()) ELSE NULL END,revision=revision+1,updated_at=now()
                WHERE company_id=%s RETURNING *''',(status,called,note,called,company_id)).fetchone()
            if status=='taken':db.execute('''INSERT INTO employee_clients(company_id,employee_id) VALUES(%s,%s)
                ON CONFLICT(company_id) DO UPDATE SET active=true,updated_at=now()''',(company_id,employee))
            else:db.execute('UPDATE employee_clients SET active=false,updated_at=now() WHERE company_id=%s',(company_id,))
            db.execute('INSERT INTO audit_events(employee_id,kind,entity_id,details) VALUES(%s,%s,%s,%s)',(employee,'lead.'+status,str(company_id),Jsonb({'before':{'status':old['status'],'called':old['called'],'call_note':old['call_note']},'after':{'status':status,'called':called,'call_note':note}})))
            return result

    def update_stage(self,employee,company_id,stage,revision):
        with self.transaction(employee) as db:
            old=db.execute('SELECT * FROM employee_clients WHERE company_id=%s AND employee_id=%s AND active FOR UPDATE',(company_id,employee)).fetchone()
            if not old:raise NotFound('Клиент не найден')
            if old['revision']!=revision:raise Conflict('Этап уже изменён. Обновите список.')
            result=db.execute('UPDATE employee_clients SET stage=%s,revision=revision+1,updated_at=now() WHERE company_id=%s RETURNING *',(stage,company_id)).fetchone()
            db.execute('INSERT INTO audit_events(employee_id,kind,entity_id,details) VALUES(%s,%s,%s,%s)',(employee,'client.stage',str(company_id),Jsonb({'before':old['stage'],'after':stage})))
            return result

    def close(self):self.pool.close()
