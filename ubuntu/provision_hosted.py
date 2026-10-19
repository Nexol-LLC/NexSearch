"""Initial local PostgreSQL provisioning. Input file holds only an Argon2 hash."""
from pathlib import Path
from uuid import uuid4
import secrets,json,sys
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
seed=json.loads(Path(sys.argv[1]).read_text());root=Path('/opt/nexsearch/current');password=secrets.token_urlsafe(40)
with psycopg.connect('dbname=postgres',autocommit=True) as conn:
 exists=bool(conn.execute("SELECT 1 FROM pg_database WHERE datname='nexsearch'").fetchone())
 if exists:
  with psycopg.connect('dbname=nexsearch') as current:
   if current.execute("SELECT to_regclass('public.employees')").fetchone()[0]:raise RuntimeError('Existing database preserved; use migration instead')
 if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='nexsearch_owner'").fetchone():conn.execute('CREATE ROLE nexsearch_owner NOLOGIN')
 if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='nexsearch_app'").fetchone():conn.execute('CREATE ROLE nexsearch_app LOGIN')
 conn.execute(sql.SQL('ALTER ROLE nexsearch_app PASSWORD {}').format(sql.Literal(password)))
 if not exists:conn.execute('CREATE DATABASE nexsearch OWNER nexsearch_owner')
with psycopg.connect('dbname=nexsearch') as conn:
 conn.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto');conn.execute('SET ROLE nexsearch_owner');conn.execute("SELECT set_config('nexol.is_admin','true',true)")
 for name in ('schema.sql','schema14.sql','schema15.sql','schema16.sql','schema17.sql'):conn.execute((root/'network'/name).read_text())
 conn.execute("INSERT INTO employees(id,login,first_name,last_name,password_hash,is_admin,role,requested_role,approved) VALUES(%s,'host','Хост','NexSearch',%s,true,'director','director',true)",(uuid4(),seed['password_hash']))
 conn.execute('GRANT USAGE ON SCHEMA public TO nexsearch_app')
 conn.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO nexsearch_app')
 conn.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO nexsearch_app')
 conn.execute('REVOKE UPDATE,DELETE ON audit_events,call_records FROM nexsearch_app')
 conn.execute("INSERT INTO audit_events(kind,entity_id,details) VALUES('server.provisioned','','{\"version\":\"1.5.0\"}')")
env=Path('/var/lib/nexsearch-provision/host.env')
env.write_text('NEXOL_DATABASE_URL="'+make_conninfo(host='127.0.0.1',dbname='nexsearch',user='nexsearch_app',password=password)+'"\nNEXOL_HOME=/var/lib/nexsearch\nNEXOL_HOST_PORT=8743\nNEXSEARCH_UPDATE_STATE=/var/lib/nexsearch-updates/status.json\nNEXSEARCH_UPDATE_PACKAGES=/var/lib/nexsearch-updates/packages\n');env.chmod(0o600)
