"""One host-side queue for eight sources; PostgreSQL owns all identities and assignments."""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from urllib.parse import urlsplit,urlencode
import xml.etree.ElementTree as ET
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from collector import Http,canonical,parse_listing,parse_company,enrich_contacts,check_fns
from extra_sources import card_url
from source_catalog import SOURCES
from fns_registry import search as registry_search,company as registry_company,BASE
from .checko import Checko,VerificationUnavailable
from .criteria import matches

LOG=logging.getLogger('nexol.search')
CONFIG=dict(request_delay_seconds=.5,timeout_seconds=12)

class SearchWorker:
    def __init__(self,db,checker=None):
        self.db=db;self.checker=checker or Checko(db);self.closed=threading.Event();self.wake=threading.Event();self.thread=None
    def start(self):
        self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
    def stop(self):
        self.closed.set();self.wake.set()
        if self.thread:self.thread.join(timeout=50)
    def loop(self):
        with psycopg.connect(self.db.pool.conninfo,autocommit=True) as lease:
            if not lease.execute('SELECT pg_try_advisory_lock(73142003)').fetchone()[0]:return
            with self.db.transaction(admin=True) as db:
                db.execute("UPDATE scraper_candidates SET state='pending',last_attempt=NULL WHERE state='processing'")
                db.execute("UPDATE scraper_candidates SET last_attempt=NULL WHERE last_attempt IN (SELECT id FROM jobs WHERE status='running')")
                db.execute("UPDATE jobs SET status='queued' WHERE status='running'")
            while not self.closed.is_set():
                with self.db.transaction(admin=True) as db:
                    job=db.execute("SELECT j.*,e.is_admin FROM jobs j JOIN employees e ON e.id=j.employee_id WHERE j.status='queued' ORDER BY j.created_at FOR UPDATE OF j SKIP LOCKED LIMIT 1").fetchone()
                    if job:db.execute("UPDATE jobs SET status='running',message='Поиск и проверка компаний' WHERE id=%s",(job['id'],))
                if not job:self.wake.wait(2);self.wake.clear();continue
                try:self.run(job)
                except Exception:
                    LOG.exception('Search failed for job %s',job['id'])
                    with self.db.transaction(admin=True) as db:db.execute("UPDATE jobs SET status='error',message='Ошибка сбора; найденные компании сохранены',finished_at=now() WHERE id=%s",(job['id'],))
    def job(self,ident):
        with self.db.transaction(admin=True) as db:return db.execute('SELECT * FROM jobs WHERE id=%s',(ident,)).fetchone()
    def message(self,ident,message):
        with self.db.transaction(admin=True) as db:db.execute('UPDATE jobs SET message=%s WHERE id=%s',(message[:500],ident))
    def cancelled(self,ident):return self.closed.is_set() or self.job(ident)['stop_requested']
    def discover(self,source,http,job_id):
        if source['id']=='egrul':
            http.request(BASE+'index.html')
            for query in source['queries']:
                page=1
                while not self.cancelled(job_id):
                    rows=registry_search(http,query,page=page)
                    with self.db.transaction(admin=True) as db:
                        for row in rows:
                            try:item=registry_company(row,source)
                            except ValueError:continue
                            db.execute('INSERT INTO scraper_candidates(url,source,payload) VALUES(%s,%s,%s) ON CONFLICT(url) DO UPDATE SET payload=excluded.payload',(item['source_url'],source['id'],Jsonb(item)))
                    yield
                    if not rows or page*20>=int(rows[0].get('cnt',len(rows))):break
                    page+=1
            return
        seed=canonical(source['url'])
        with self.db.transaction(admin=True) as db:
            known=[r['url'] for r in db.execute('SELECT url FROM scraper_pages WHERE source=%s ORDER BY last_scan NULLS FIRST,url',(source['id'],))]
        pages=list(dict.fromkeys([seed,*known,*source.get('sitemaps',[])]));visited=set()
        while pages and not self.cancelled(job_id):
            url=pages.pop(0)
            if url in visited:continue
            visited.add(url)
            if not http.allowed(url):continue
            self.message(job_id,'Каталог: '+source['name'])
            response=http.get(url)
            if urlsplit(url).path.endswith('.xml'):
                xml=ET.fromstring(response.content)
                locs=[canonical(n.text.strip()) for n in xml.iter() if n.tag.rsplit('}',1)[-1]=='loc' and n.text]
                locs=[u for u in locs if urlsplit(u).hostname==urlsplit(seed).hostname]
                if xml.tag.rsplit('}',1)[-1]=='sitemapindex':urls=[];next_pages=locs
                else:urls=[u for u in locs if card_url(source,u)];next_pages=[]
            else:
                urls,next_pages=parse_listing(source,response.text,url)
                if not urls:raise ValueError('В каталоге не найдены карточки')
            with self.db.transaction(admin=True) as db:
                for card in urls:db.execute('INSERT INTO scraper_candidates(url,source) VALUES(%s,%s) ON CONFLICT DO NOTHING',(card,source['id']))
                db.execute('INSERT INTO scraper_pages(url,source,last_scan) VALUES(%s,%s,now()) ON CONFLICT(url) DO UPDATE SET last_scan=now()',(url,source['id']))
            pages.extend(u for u in next_pages if u not in visited and u not in pages)
            yield
    def run_legacy(self,job):
        ident=job['id'];owner=None if job['is_admin'] else job['employee_id'];target=job['target']
        if owner:self.db.claim_available(owner,max(0,(target or 100)-job['added']),ident)
        http=Http(CONFIG);generators={s['id']:self.discover(s,http,ident) for s in SOURCES};exhausted=set();warnings=[]
        while not self.cancelled(ident):
            current=self.job(ident)
            if target is not None and current['added']>=target:break
            progressed=False
            for source in SOURCES:
                if self.cancelled(ident):break
                if target is not None and self.job(ident)['added']>=target:break
                if source['id'] in exhausted:continue
                def candidate():
                    with self.db.transaction(admin=True) as db:
                        return db.execute("SELECT * FROM scraper_candidates WHERE source=%s AND state IN ('pending','failed') AND last_attempt IS DISTINCT FROM %s ORDER BY attempts,url LIMIT 1",(source['id'],ident)).fetchone()
                card=candidate()
                if not card:
                    try:next(generators[source['id']]);progressed=True;card=candidate()
                    except StopIteration:exhausted.add(source['id']);continue
                    except Exception as ex:
                        LOG.warning('%s: %s',source['name'],ex);warnings.append(source['name']);exhausted.add(source['id']);continue
                if not card:continue
                progressed=True
                try:
                    if source['id']=='egrul':item=card['payload']
                    else:
                        item=parse_company(source,http.get(card['url']).text,card['url'])
                        if source['id']=='zavody':
                            try:
                                details=parse_company(source,http.get(card['url'].rstrip('/')+'/details').text,card['url'])
                                for key in ('name','inn','ogrn','kpp'):
                                    if details.get(key):item[key]=details[key]
                                item['extra'].update(details['extra'])
                            except Exception as ex:item['notes'].append('Реквизиты каталога: '+str(ex))
                        self.message(ident,'В обработке: '+item['name'])
                        enrich_contacts(item,http);check_fns(item,http)
                    if self.cancelled(ident):break
                    company_id,is_new,assigned=self.db.ingest(item,owner,processing=True,job=ident)
                    with self.db.transaction(admin=True) as db:
                        db.execute("UPDATE employee_leads SET status='new',updated_at=now() WHERE company_id=%s AND status='processing' AND revision=0",(company_id,))
                        db.execute("UPDATE scraper_candidates SET state='done',attempts=attempts+1,last_attempt=%s,error=NULL WHERE url=%s",(ident,card['url']))
                        if not (assigned if owner else is_new):db.execute('UPDATE jobs SET duplicates=duplicates+1 WHERE id=%s',(ident,))
                except Exception as ex:
                    LOG.warning('Card %s: %s',card['url'],ex)
                    with self.db.transaction(admin=True) as db:db.execute("UPDATE scraper_candidates SET state='failed',attempts=attempts+1,last_attempt=%s,error=%s WHERE url=%s",(ident,str(ex)[:500],card['url']))
            if not progressed:break
        current=self.job(ident);stopped=self.cancelled(ident)
        state='stopped' if stopped else 'partial' if target is not None and current['added']<target else 'completed'
        message=f"Добавлено {current['added']} компаний. Пропущено повторов: {current['duplicates']}."
        if warnings:message+=' Недоступны: '+', '.join(sorted(set(warnings)))+'.'
        with self.db.transaction(admin=True) as db:db.execute('UPDATE jobs SET status=%s,message=%s,finished_at=now() WHERE id=%s',(state,message,ident))

    def reject(self,job,reason):
        with self.db.transaction(admin=True) as db:
            db.execute('UPDATE jobs SET rejected=rejected+1,rejection_reasons=jsonb_set(rejection_reasons,ARRAY[%s],to_jsonb(COALESCE((rejection_reasons->>%s)::int,0)+1),true) WHERE id=%s',(reason,reason,job))

    def process_card(self,card,source,job,owner,criteria):
        http=Http(CONFIG);ident=job['id']
        try:
            if self.cancelled(ident):return
            item=card['payload'] if source['id']=='egrul' else parse_company(source,http.get(card['url']).text,card['url'])
            if source['id']=='zavody' and not item.get('inn'):
                try:
                    details=parse_company(source,http.get(card['url'].rstrip('/')+'/details').text,card['url'])
                    for key in ('inn','ogrn','kpp'):
                        if details.get(key):item[key]=details[key]
                except Exception:pass
            from collector import valid_inn
            if not valid_inn(item.get('inn')):
                # Contact pages often contain the INN; recover it before rejecting.
                enrich_contacts(item,http)
            if not valid_inn(item.get('inn')) or len(item.get('inn',''))!=10:
                self.reject(ident,'Без ИНН');return
            self.checker.enrich(item)
            with self.db.transaction(admin=True) as db:db.execute('UPDATE jobs SET verified=verified+1 WHERE id=%s',(ident,))
            if not item['verification']['active']:self.reject(ident,'Недействующая');return
            if criteria.get('require_contacts') and not(item.get('phones') or item.get('emails')):enrich_contacts(item,http)
            if not matches(item,criteria):
                self.db.ingest(item);self.reject(ident,'Не соответствует критериям');return
            if not(item.get('phones') or item.get('emails')):enrich_contacts(item,http)
            if self.cancelled(ident):return
            company,is_new,assigned=self.db.ingest(item,owner,processing=True,job=ident)
            with self.db.transaction(admin=True) as db:
                db.execute("UPDATE employee_leads SET status='new',updated_at=now() WHERE company_id=%s AND status='processing' AND revision=0",(company,))
                if not(assigned if owner else is_new):db.execute('UPDATE jobs SET duplicates=duplicates+1 WHERE id=%s',(ident,))
            self.db.audit(owner,'search.company',company,{'name':item['name'],'inn':item['inn'],'source':item['source']})
        except VerificationUnavailable as ex:
            self.reject(ident,'Проверка недоступна')
            with self.db.transaction(admin=True) as db:db.execute("UPDATE scraper_candidates SET state='failed',error=%s WHERE url=%s",(str(ex)[:300],card['url']))
            return
        except Exception as ex:
            with self.db.transaction(admin=True) as db:db.execute("UPDATE scraper_candidates SET state='failed',error=%s WHERE url=%s",(type(ex).__name__,card['url']))
            LOG.warning('Source card failed (%s)',type(ex).__name__);return
        finally:
            with self.db.transaction(admin=True) as db:db.execute("UPDATE scraper_candidates SET state=CASE WHEN state='processing' THEN %s ELSE state END WHERE url=%s",('pending' if self.cancelled(ident) else 'done',card['url']))

    def run(self,job):
        ident=job['id'];owner=job['employee_id'];target=job['target'];criteria=job.get('criteria') or {}
        if not self.checker.key():
            with self.db.transaction(admin=True) as db:db.execute("UPDATE jobs SET status='error',finished_at=now(),message='Настройте API-ключ Чекко в настройках хоста' WHERE id=%s",(ident,))
            return
        # Refresh unassigned records whose legal/financial verification has expired.
        with self.db.transaction(admin=True) as db:
            stale=db.execute("SELECT payload FROM companies c WHERE c.verified_at<now()-interval '24 hours' AND NOT EXISTS(SELECT 1 FROM employee_leads l WHERE l.company_id=c.id) ORDER BY verified_at LIMIT %s",(target or 100,)).fetchall()
        for row in stale:
            if self.cancelled(ident):break
            item=row['payload'];self.checker.enrich(item);self.db.ingest(item)
        if target is None:
            while not self.cancelled(ident) and self.db.claim_available(owner,100,ident,criteria):pass
        else:self.db.claim_available(owner,max(0,target-job['added']),ident,criteria)
        if target is not None and self.job(ident)['added']>=target:
            with self.db.transaction(admin=True) as db:db.execute("UPDATE jobs SET status='completed',finished_at=now(),message='Добавлены проверенные компании из общей базы' WHERE id=%s",(ident,))
            self.db.audit(owner,'search.finished',ident,{'added':target,'status':'completed','cached':True})
            return
        generators={s['id']:self.discover(s,Http(CONFIG),ident) for s in SOURCES};exhausted=set();warnings=[]
        def discover_next(source):
            try:next(generators[source['id']]);return True
            except StopIteration:return False
            except Exception:return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            # Independent catalogues can be read in parallel; each has its own HTTP session.
            discovery={pool.submit(discover_next,source):source for source in SOURCES}
            with ThreadPoolExecutor(max_workers=3) as early:
                active=[]
                for future in as_completed(discovery):
                    source=discovery[future];result=future.result()
                    if hasattr(self,'progress_source'):self.progress_source(job,source,'ready' if result else 'unavailable')
                    if result is not True:exhausted.add(source['id'])
                    if result is None:warnings.append(source['name'])
                    if self.cancelled(ident) or target is not None and self.job(ident)['added']>=target:continue
                    with self.db.transaction(admin=True) as db:
                        card=db.execute("SELECT * FROM scraper_candidates WHERE source=%s AND state IN ('pending','failed') AND last_attempt IS DISTINCT FROM %s ORDER BY attempts,url FOR UPDATE SKIP LOCKED LIMIT 1",(source['id'],ident)).fetchone()
                        if card:db.execute("UPDATE scraper_candidates SET state='processing',last_attempt=%s,attempts=attempts+1,error=NULL WHERE url=%s",(ident,card['url']))
                    if card:active.append(early.submit(self.process_card,card,source,job,owner,criteria))
                for result in active:result.result()
            while not self.cancelled(ident):
                current=self.job(ident)
                if target is not None and current['added']>=target:break
                cards=[];progressed=False
                for source in SOURCES:
                    with self.db.transaction(admin=True) as db:
                        card=db.execute("SELECT * FROM scraper_candidates WHERE source=%s AND state IN ('pending','failed') AND last_attempt IS DISTINCT FROM %s ORDER BY attempts,url FOR UPDATE SKIP LOCKED LIMIT 1",(source['id'],ident)).fetchone()
                        if card:db.execute("UPDATE scraper_candidates SET state='processing',last_attempt=%s,attempts=attempts+1,error=NULL WHERE url=%s",(ident,card['url']))
                    if not card and source['id'] not in exhausted:
                        result=discover_next(source);progressed|=result is True
                        if result is not True:exhausted.add(source['id'])
                        if result is None:warnings.append(source['name'])
                    if card:cards.append((card,source));progressed=True
                if cards:
                    self.message(ident,'Проверяем ИНН, статус и финансовую отчётность')
                    futures=[pool.submit(self.process_card,card,source,job,owner,criteria) for card,source in cards]
                    for future in futures:future.result()
                if not progressed:break
        current=self.job(ident);stopped=self.cancelled(ident)
        state='stopped' if stopped else 'partial' if target is not None and current['added']<target else 'completed'
        message=f"Добавлено {current['added']}. Отсеяно {current['rejected']}. Повторов {current['duplicates']}."
        if warnings:message+=' Недоступны: '+', '.join(set(warnings))+'.'
        with self.db.transaction(admin=True) as db:db.execute('UPDATE jobs SET status=%s,message=%s,finished_at=now() WHERE id=%s',(state,message,ident))
        self.db.audit(job['employee_id'],'search.finished',ident,{'added':current['added'],'rejected':current['rejected'],'status':state})
