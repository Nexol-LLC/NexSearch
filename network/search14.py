"""Three independent jobs share global deduplication and per-origin rate limits."""
import threading,time,logging
from concurrent.futures import ThreadPoolExecutor,as_completed
from urllib.parse import urlsplit
import psycopg
from .search import SearchWorker as BaseWorker,CONFIG,Http,SOURCES

LOG=logging.getLogger('nexsearch.search')
_lock=threading.Lock();_hosts={}
class LimitedHttp(Http):
 def request(self,url,*args,**kwargs):
  host=urlsplit(url).hostname
  with _lock:gate=_hosts.setdefault(host,{'lock':threading.RLock(),'last':0.})
  with gate['lock']:
   wait=max(CONFIG['request_delay_seconds'],self.delays.get(host,0))-(time.monotonic()-gate['last'])
   if wait>0:time.sleep(wait)
   try:return super().request(url,*args,**kwargs)
   finally:gate['last']=time.monotonic()

class SearchWorker(BaseWorker):
 def loop(self):
  with psycopg.connect(self.db.pool.conninfo,autocommit=True) as lease:
   if not lease.execute('SELECT pg_try_advisory_lock(73142003)').fetchone()[0]:return
   with self.db.transaction(admin=True) as conn:
    conn.execute("UPDATE scraper_candidates SET state='pending',last_attempt=NULL WHERE state='processing'")
    conn.execute("UPDATE jobs SET status='queued' WHERE status='running'")
   with ThreadPoolExecutor(max_workers=3) as pool:
    pending={}
    while not self.closed.is_set():
     for future in list(pending):
      if future.done():
       ident=pending.pop(future)
       try:future.result()
       except Exception:
        LOG.exception('Search job failed')
        with self.db.transaction(admin=True) as conn:conn.execute("UPDATE jobs SET status='error',message='Ошибка источника; найденные компании сохранены',finished_at=now() WHERE id=%s",(ident,))
     if len(pending)<3:
      with self.db.transaction(admin=True) as conn:
       job=conn.execute("SELECT j.*,e.is_admin FROM jobs j JOIN employees e ON e.id=j.employee_id WHERE j.status='queued' AND e.active AND (e.approved OR e.is_admin) ORDER BY j.created_at FOR UPDATE OF j SKIP LOCKED LIMIT 1").fetchone()
       if job:conn.execute("UPDATE jobs SET status='running',message='Поиск и проверка компаний' WHERE id=%s",(job['id'],))
      if job:pending[pool.submit(self.run,job)]=job['id'];continue
     self.wake.wait(.5);self.wake.clear()
    for f in pending:f.cancel()
 def message(self,ident,message):
  super().message(ident,message)
  job=self.job(ident);self.db.events.publish(job['employee_id'],'search.progress')
 def progress_source(self,job,source,state):
  from psycopg.types.json import Jsonb
  with self.db.transaction(admin=True) as conn:
   conn.execute('UPDATE jobs SET source_progress=jsonb_set(source_progress,ARRAY[%s],%s,true) WHERE id=%s',(source['id'],Jsonb({'name':source['name'],'state':state}),job['id']))
 def process_card(self,card,source,job,owner,criteria):
  with self.db.transaction(admin=True) as conn:conn.execute('UPDATE jobs SET scanned=scanned+1 WHERE id=%s',(job['id'],))
  # The base routine uses the shared LimitedHttp binding installed below.
  super().process_card(card,source,job,owner,criteria)
  self.db.events.publish(owner,'search.progress')

# Preserve extraction and verification in one implementation, with aggregate pacing.
from . import search as base_search
base_search.Http=LimitedHttp
