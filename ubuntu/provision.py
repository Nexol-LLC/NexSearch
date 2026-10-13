"""Run as postgres on initial install. No credentials in process arguments."""
from pathlib import Path
from uuid import uuid4
import secrets,sys,json,getpass,shutil
sys.path.insert(0,'/opt/nexsearch')
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from network.auth import HASHER
root=Path('/opt/nexsearch');home=Path('/var/lib/nexsearch');password=secrets.token_urlsafe(40)
first=input('Имя директора: ').strip();last=input('Фамилия директора: ').strip()
while True:
 login=input('Логин директора (латиница, не короче 3): ').strip()
 import re
 if re.fullmatch('[a-zA-Z0-9._-]{3,64}',login):break
while True:
 human=getpass.getpass('Пароль директора (минимум 8 символов): ')
 if len(human)>=8 and human==getpass.getpass('Повторите пароль: '):break
 print('Пароли не совпали или слишком короткие.')
with psycopg.connect('dbname=postgres',autocommit=True) as conn:
 addresses=conn.execute('SHOW listen_addresses').fetchone()[0]
 if any(a.strip() not in ('localhost','127.0.0.1','::1') for a in addresses.split(',')):
  raise RuntimeError('Ограничьте listen_addresses PostgreSQL локальными адресами и повторите установку.')
 conn.execute('CREATE ROLE nexsearch_owner NOLOGIN')
 conn.execute(sql.SQL('CREATE ROLE nexsearch_app LOGIN PASSWORD {}').format(sql.Literal(password)))
 conn.execute('CREATE DATABASE nexsearch OWNER nexsearch_owner')
with psycopg.connect('dbname=nexsearch') as conn:
 conn.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto')
 conn.execute('SET ROLE nexsearch_owner')
 conn.execute("SELECT set_config('nexol.is_admin','true',true)")
 conn.execute((root/'network/schema.sql').read_text(encoding='utf-8'))
 conn.execute((root/'network/schema14.sql').read_text(encoding='utf-8'))
 conn.execute("INSERT INTO employees(id,login,first_name,last_name,password_hash,is_admin,role,requested_role) VALUES(%s,%s,%s,%s,%s,true,'director','director')",(uuid4(),login,first,last,HASHER.hash(human)))
 conn.execute('GRANT USAGE ON SCHEMA public TO nexsearch_app')
 conn.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO nexsearch_app')
 conn.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO nexsearch_app')
 conn.execute('REVOKE UPDATE,DELETE ON audit_events FROM nexsearch_app')
# postgres user can write only staging; root shell installs the protected files.
target=Path('/var/lib/nexsearch-provision')
(target/'host.env').write_text('NEXOL_DATABASE_URL="'+make_conninfo(host='127.0.0.1',dbname='nexsearch',user='nexsearch_app',password=password)+'"\nNEXOL_HOME=/var/lib/nexsearch\n',encoding='utf-8')
(target/'host.env').chmod(0o600)
