"""Verified local PostgreSQL backups; each restore check uses a separate scratch database."""
from pathlib import Path
from datetime import datetime,timezone
from uuid import uuid4
import os,threading,time,hashlib,subprocess,json,shutil,logging
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from . import secure_store
from .postgres_runtime import transfer_database,pg_binary

class Backups:
 def __init__(self,db,home=None,runtime=None):
  self.db=db;self.home=Path(home or os.environ.get('NEXOL_HOME','.'));self.runtime=Path(runtime or os.environ.get('NEXOL_POSTGRES_RUNTIME','postgres'))
  self.gate=threading.Lock();self.closed=threading.Event();self.thread=None
 def available(self):return (self.home/'postgres.sec').exists() and pg_binary(self.runtime,'pg_dump').exists()
 def start(self):
  if not self.available():return
  self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
 def close(self):
  self.closed.set()
  if self.thread:self.thread.join(timeout=60)
 def loop(self):
  while not self.closed.wait(60):
   try:
    with self.db.transaction(admin=True) as c:recent=c.execute("SELECT 1 FROM backup_records WHERE status='verified' AND created_at>now()-interval '24 hours'").fetchone()
    if not recent:self.create()
   except Exception:logging.getLogger('nexsearch.backups').exception('Backup check failed; retrying on the next cycle')
 def create(self,employee=None):
  if not self.available():raise ValueError('На Ubuntu резервные копии выполняются службой PostgreSQL; используйте комплектный backup.sh')
  if not self.gate.acquire(blocking=False):raise ValueError('Резервное копирование уже выполняется')
  ident=uuid4();folder=(self.home/'backups').resolve();folder.mkdir(exist_ok=True)
  if os.name!='nt':folder.chmod(0o700)
  file=folder/(datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+str(ident)+'.dump')
  with self.db.transaction(admin=True) as c:c.execute('INSERT INTO backup_records(id,path,status) VALUES(%s,%s,\'running\')',(ident,str(file)))
  try:
   transfer_database(self.home,self.runtime,file)
   digest=hashlib.sha256(file.read_bytes()).hexdigest();self.verify_restore(file)
   with self.db.transaction(admin=True) as c:c.execute("UPDATE backup_records SET status='verified',size_bytes=%s,sha256=%s,verified_at=now() WHERE id=%s",(file.stat().st_size,digest,ident))
   # Recovery also needs the host identity and host-only secret settings.
   protected=folder/'identity';protected.mkdir(exist_ok=True)
   if os.name!='nt':protected.chmod(0o700)
   for name in ('host-key.pem','host-cert.pem','host-identity.json'):
    p=self.home/'identity'/name
    if p.exists():shutil.copy2(p,protected/name)
   if (self.home/'host-secrets.enc').exists():shutil.copy2(self.home/'host-secrets.enc',protected/'host-secrets.enc')
   self.db.audit(employee,'backup.verified',ident,{'size_bytes':file.stat().st_size,'sha256':digest});return {'id':ident,'status':'verified','size_bytes':file.stat().st_size}
  except Exception as error:
   with self.db.transaction(admin=True) as c:c.execute("UPDATE backup_records SET status='failed',error=%s WHERE id=%s",(type(error).__name__,ident))
   self.db.audit(employee,'backup.failed',ident,{'error':type(error).__name__});raise ValueError('Резервная копия не прошла проверку восстановления') from None
  finally:self.gate.release()
 def verify_restore(self,file):
  settings=secure_store.read(self.home/'postgres.sec');scratch='nexsearch_verify_'+uuid4().hex
  admin=make_conninfo(host='127.0.0.1',port=settings['port'],user='nexol_admin',password=settings['admin_password'],dbname='postgres')
  env=os.environ.copy();env['PGPASSWORD']=settings['admin_password'];env.pop('PGOPTIONS',None)
  with psycopg.connect(admin,autocommit=True) as c:c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(scratch)))
  try:
   args=[str(pg_binary(self.runtime,'pg_restore')),'-h','127.0.0.1','-p',str(settings['port']),'-U','nexol_admin','-d',scratch,'--exit-on-error','--no-owner','--no-privileges',str(file)]
   result=subprocess.run(args,env=env,capture_output=True,timeout=180,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
   if result.returncode:raise RuntimeError('Restore verification failed')
   with psycopg.connect(make_conninfo(host='127.0.0.1',port=settings['port'],user='nexol_admin',password=settings['admin_password'],dbname=scratch)) as c:
    assert c.execute('SELECT 1 FROM schema_meta WHERE version=14').fetchone()
    # Verify relational consistency, not only that the archive is readable.
    assert c.execute('SELECT count(*) FROM employee_clients cl LEFT JOIN companies co ON co.id=cl.company_id WHERE co.id IS NULL').fetchone()[0]==0
  finally:
   with psycopg.connect(admin,autocommit=True) as c:
    c.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s',(scratch,))
    c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(scratch)))
