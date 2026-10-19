"""Four employee searches share paced sources, verification cache and unique leads."""
import threading,time,logging
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from urllib.parse import urlsplit
import psycopg
from .search import SearchWorker as BaseWorker,CONFIG,Http,SOURCES
from .cancellation import scope,check,wait as pause,SearchCancelled
from .scheduling import enqueue_due
from .update_maintenance import can_start_search
LOG=logging.getLogger('nexsearch.search')
_lock=threading.Lock();_hosts={}
class LimitedHttp(Http):
 def request(self,url,*args,**kwargs):
  check();host=urlsplit(url).hostname
  with _lock:gate=_hosts.setdefault(host,{'lock':threading.RLock(),'last':0.})
  while not gate['lock'].acquire(timeout=.1):check()
  try:
   pause(max(CONFIG['request_delay_seconds'],self.delays.get(host,0))-(time.monotonic()-gate['last']))
   check();return super().request(url,*args,**kwargs)
  finally:gate['last']=time.monotonic();gate['lock'].release()

class SearchWorker(BaseWorker):
 def __init__(self,*args,**kwargs):
  super().__init__(*args,**kwargs);self.cancel_lock=threading.Lock();self.cancel_events={};self.discovery_locks={s['id']:threading.Lock() for s in SOURCES};self.discovery_recent={}
 def event(self,ident):
  with self.cancel_lock:return self.cancel_events.setdefault(str(ident),threading.Event())
 def cancel(self,ident):self.event(ident).set();self.wake.set()
 def stop(self):
  with self.cancel_lock:
   for event in self.cancel_events.values():event.set()
  super().stop()
 def loop(self):
  while not self.closed.is_set():
   try:self._serve_once()
   except Exception:LOG.exception('Search coordinator interrupted; reconnecting without discarding queued jobs')
   with self.cancel_lock:self.cancel_events.clear()
   self.closed.wait(2)
 @contextmanager
 def executor(self):
  with ThreadPoolExecutor(max_workers=4,thread_name_prefix='employee-search') as pool:
   try:yield pool
   finally:
    with self.cancel_lock:
     for event in self.cancel_events.values():event.set()
 def _serve_once(self):
  with psycopg.connect(self.db.pool.conninfo,autocommit=True) as lease:
   if not lease.execute('SELECT pg_try_advisory_lock(73142003)').fetchone()[0]:return
   with self.db.transaction(admin=True) as conn:
    conn.execute("UPDATE scraper_candidates SET state='pending',last_attempt=NULL WHERE state='processing'")
    conn.execute("UPDATE jobs SET status='queued' WHERE status='running' AND NOT stop_requested")
    conn.execute("UPDATE jobs SET status='stopped',finished_at=now() WHERE stop_requested AND status IN ('queued','running')")
   with self.executor() as pool:
    pending={};heartbeat=0.
    while not self.closed.is_set():
     if time.monotonic()-heartbeat>=5:lease.execute('SELECT 1');heartbeat=time.monotonic()
     enqueue_due(self.db)
     for future in list(pending):
      if future.done():
       ident=pending.pop(future)
       try:future.result()
       except Exception:
        LOG.exception('Search job failed')
        with self.db.transaction(admin=True) as conn:conn.execute("UPDATE jobs SET status='error',message='Ошибка источника; найденные компании сохранены',finished_at=now() WHERE id=%s AND NOT stop_requested",(ident,))
       with self.cancel_lock:self.cancel_events.pop(str(ident),None)
     if len(pending)<4:
      job=None
      with self.db.transaction(admin=True) as conn:
       if can_start_search(conn):job=conn.execute("SELECT j.*,e.is_admin FROM jobs j JOIN employees e ON e.id=j.employee_id WHERE j.status='queued' AND NOT j.stop_requested AND e.active AND (e.approved OR e.is_admin) ORDER BY j.created_at FOR UPDATE OF j SKIP LOCKED LIMIT 1").fetchone()
       if job:conn.execute("UPDATE jobs SET status='running',message='Поиск и проверка компаний' WHERE id=%s",(job['id'],))
      if job:pending[pool.submit(self.run,job)]=job['id'];continue
     self.wake.wait(.25);self.wake.clear()
 def message(self,ident,message):
  with self.db.transaction(admin=True) as c:c.execute('UPDATE jobs SET message=%s WHERE id=%s AND NOT stop_requested',(message[:500],ident))
  job=self.job(ident);self.db.events.publish(job['employee_id'],'search.progress')
 def progress_source(self,job,source,state):
  from psycopg.types.json import Jsonb
  with self.db.transaction(admin=True) as c:c.execute('UPDATE jobs SET source_progress=jsonb_set(source_progress,ARRAY[%s],%s,true) WHERE id=%s',(source['id'],Jsonb({'name':source['name'],'state':state}),job['id']))
 def process_card(self,card,source,job,owner,criteria):
  try:
   with scope(self.event(job['id'])):
    with self.db.transaction(admin=True) as c:c.execute('UPDATE jobs SET scanned=scanned+1 WHERE id=%s',(job['id'],))
    super().process_card(card,source,job,owner,criteria)
  except SearchCancelled:pass
  finally:self.db.events.publish(owner,'search.progress')
 def take_card(self,source,ident):
  with self.db.transaction(admin=True) as c:
   card=c.execute("SELECT * FROM scraper_candidates WHERE source=%s AND state IN ('pending','failed') AND last_attempt IS DISTINCT FROM %s ORDER BY attempts,url FOR UPDATE SKIP LOCKED LIMIT 1",(source['id'],ident)).fetchone()
   if card:c.execute("UPDATE scraper_candidates SET state='processing',last_attempt=%s,attempts=attempts+1,error=NULL WHERE url=%s",(ident,card['url']))
   return card
 def run(self,job):
  ident=job['id'];owner=job['employee_id'];target=job['target'] or 50;criteria=job.get('criteria') or {};event=self.event(ident)
  if self.cancelled(ident):event.set();return
  if not self.checker.key():
   with self.db.transaction(admin=True) as c:c.execute("UPDATE jobs SET status='error',finished_at=now(),message='Настройте API-ключ Чекко в настройках хоста' WHERE id=%s AND NOT stop_requested",(ident,))
   return
  self.db.claim_available(owner,target,ident,criteria)
  # Revalidate surplus records from earlier runs after their 24-hour legal check expires.
  if self.job(ident)['added']<target and not self.cancelled(ident) and not getattr(self.checker,'failure',lambda:'')():
   with self.db.transaction(admin=True) as c:stale=c.execute("SELECT c.payload FROM companies c WHERE c.verified_at<now()-interval '24 hours' AND NOT EXISTS(SELECT 1 FROM employee_leads l WHERE l.company_id=c.id) ORDER BY c.verified_at LIMIT %s",(target,)).fetchall()
   if stale:
    with ThreadPoolExecutor(max_workers=4) as refresh:
     list(refresh.map(lambda r:self.process_card({'url':r['payload']['source_url'],'payload':r['payload']},{'id':'egrul'},job,owner,criteria),stale))
  generators={s['id']:self.discover(s,LimitedHttp(CONFIG),ident) for s in SOURCES};exhausted=set();warnings=[]
  provider_error=''
  def discover_next(source):
   key=source['id'];lock=self.discovery_locks[key]
   if not lock.acquire(blocking=False):return 'busy'
   try:
    if time.monotonic()-self.discovery_recent.get(key,0)<2:return 'busy'
    with scope(event):next(generators[key])
    self.discovery_recent[key]=time.monotonic();return 'ready'
   except StopIteration:return 'exhausted'
   except SearchCancelled:return 'cancelled'
   except Exception:return 'unavailable'
   finally:lock.release()
  with ThreadPoolExecutor(max_workers=4,thread_name_prefix='source-search') as pool:
   active={};rotation=0
   while not self.cancelled(ident):
    current=self.job(ident)
    if current['added']>=target:break
    provider_error=getattr(self.checker,'failure',lambda:'')()
    if provider_error:break
    progressed=False
    for source in SOURCES[rotation:]+SOURCES[:rotation]:
     if len(active)>=4:break
     card=self.take_card(source,ident)
     if card:active[pool.submit(self.process_card,card,source,job,owner,criteria)]=None;progressed=True
     elif source['id'] not in exhausted and source['id'] not in active.values():active[pool.submit(discover_next,source)]=source['id'];progressed=True
    rotation=(rotation+1)%len(SOURCES)
    if not active:
     if not progressed:break
     event.wait(.1);continue
    done,_=wait(active,timeout=.15,return_when=FIRST_COMPLETED)
    for f in done:
     source_id=active.pop(f);result=f.result()
     if source_id:
      source=next(s for s in SOURCES if s['id']==source_id);self.progress_source(job,source,result)
      if result in ('exhausted','unavailable'):exhausted.add(source_id)
      if result=='unavailable':warnings.append(source['name'])
      if result=='busy':event.wait(.1)
   if self.cancelled(ident):
    event.set()
    for future in active:future.cancel()
  current=self.job(ident);stopped=self.cancelled(ident)
  state='stopped' if stopped else 'error' if provider_error and not current['added'] else 'partial' if current['added']<target else 'completed'
  message=f"Добавлено {current['added']}. Отсеяно {current['rejected']}. Повторов {current['duplicates']}."
  if warnings:message+=' Недоступны: '+', '.join(sorted(set(warnings)))+'.'
  if provider_error:message+=' Чекко: '+provider_error+'. Найденные компании сохранены.'
  with self.db.transaction(admin=True) as c:
   c.execute("UPDATE scraper_candidates SET state='pending',last_attempt=NULL WHERE state='processing' AND last_attempt=%s",(ident,))
   c.execute('UPDATE jobs SET status=%s,message=%s,finished_at=now() WHERE id=%s',(state,message,ident))
  self.db.audit(owner,'search.finished',ident,{'added':current['added'],'status':state})

from . import search as base_search
base_search.Http=LimitedHttp
