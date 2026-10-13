"""Run as the OS postgres user; restore every dump into a throwaway database."""
from pathlib import Path
from uuid import uuid4
from datetime import datetime,timezone
import hashlib,subprocess,sys
sys.path.insert(0,'/opt/nexsearch')
import psycopg
from psycopg import sql
home=Path('/var/lib/nexsearch-backups');home.mkdir(exist_ok=True)
ident=uuid4();scratch='nexsearch_verify_'+uuid4().hex
dump=home/(datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+str(ident)+'.dump')
with psycopg.connect('dbname=nexsearch') as c:c.execute("INSERT INTO backup_records(id,path,status) VALUES(%s,%s,'running')",(ident,str(dump)))
created=False
try:
 subprocess.run(['pg_dump','--format=custom','--file',str(dump),'nexsearch'],check=True,timeout=600)
 with psycopg.connect('dbname=postgres',autocommit=True) as c:c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(scratch)));created=True
 subprocess.run(['pg_restore','--exit-on-error','--no-owner','--no-privileges','--dbname',scratch,str(dump)],check=True,timeout=600)
 with psycopg.connect('dbname='+scratch) as c:
  assert c.execute('SELECT 1 FROM schema_meta WHERE version=14').fetchone()
  assert c.execute('SELECT count(*) FROM employee_clients cl LEFT JOIN companies co ON co.id=cl.company_id WHERE co.id IS NULL').fetchone()[0]==0
 digest=hashlib.sha256(dump.read_bytes()).hexdigest()
 with psycopg.connect('dbname=nexsearch') as c:
  c.execute("UPDATE backup_records SET status='verified',size_bytes=%s,sha256=%s,verified_at=now() WHERE id=%s",(dump.stat().st_size,digest,ident))
  c.execute("INSERT INTO audit_events(kind,entity_id,details) VALUES('backup.verified',%s,jsonb_build_object('sha256',%s::text))",(str(ident),digest))
 print('Verified backup:',dump.name)
except Exception as error:
 with psycopg.connect('dbname=nexsearch') as c:c.execute("UPDATE backup_records SET status='failed',error=%s WHERE id=%s",(type(error).__name__,ident))
 raise
finally:
 if created:
  with psycopg.connect('dbname=postgres',autocommit=True) as c:
   c.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s',(scratch,))
   c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(scratch)))
