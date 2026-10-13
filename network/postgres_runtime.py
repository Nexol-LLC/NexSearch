from pathlib import Path
import os
import secrets
import subprocess
import time
import shlex
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from . import secure_store

def pg_binary(runtime,name):
    return Path(runtime)/'bin'/(name+('.exe' if os.name=='nt' else ''))

def execute(binary,args,home):
    log=Path(home)/('pg-control-'+str(time.time_ns())+'.log')
    with log.open('wb') as output:
        result=subprocess.run([str(binary),*map(str,args)],cwd=home,stdout=output,stderr=output,stdin=subprocess.DEVNULL,timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    text=log.read_text(encoding='utf-8',errors='replace')
    if result.returncode:raise RuntimeError('PostgreSQL: '+text[-2000:])
    return text

def start_postgres(home,runtime,port=54341):
    home=Path(home).resolve();home.mkdir(parents=True,exist_ok=True)
    runtime=Path(runtime).resolve();ctl=pg_binary(runtime,'pg_ctl')
    if not ctl.exists():raise RuntimeError('Не найден комплект PostgreSQL рядом с программой хоста')
    data=home/'postgres-data';secret=home/'postgres.sec'
    settings=secure_store.read(secret)
    if settings is None:
        settings=dict(admin_password=secrets.token_urlsafe(32),app_password=secrets.token_urlsafe(32),port=port)
        secure_store.write(secret,settings)
    port=settings['port']
    if not (data/'PG_VERSION').exists():
        password_file=home/'postgres-init-password.tmp'
        password_file.write_text(settings['admin_password'],encoding='utf-8')
        try:
            password_file.chmod(0o600) if os.name!='nt' else None
            arguments=['-U','nexol_admin','-A','scram-sha-256','--pwfile',str(password_file),'-E','UTF8','--locale=C']
            options=subprocess.list2cmdline(arguments) if os.name=='nt' else shlex.join(arguments)
            execute(ctl,['initdb','-D',data,'-o',options],home)
        finally:password_file.unlink(missing_ok=True)
        with (data/'postgresql.conf').open('a',encoding='utf-8') as out:
            out.write(f"\nlisten_addresses='127.0.0.1'\nport={port}\npassword_encryption='scram-sha-256'\n")
    status=subprocess.run([str(ctl),'-D',str(data),'status'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if status.returncode:execute(ctl,['-D',data,'-l',home/'postgres.log','-w','start'],home)
    admin_dsn=make_conninfo(host='127.0.0.1',port=port,user='nexol_admin',password=settings['admin_password'],dbname='postgres')
    with psycopg.connect(admin_dsn,autocommit=True) as db:
        if not db.execute("SELECT 1 FROM pg_roles WHERE rolname='nexol_app'").fetchone():
            db.execute(sql.SQL('CREATE ROLE nexol_app LOGIN PASSWORD {}').format(sql.Literal(settings['app_password'])))
        if not db.execute("SELECT 1 FROM pg_database WHERE datname='nexol'").fetchone():db.execute('CREATE DATABASE nexol OWNER nexol_app')
    return make_conninfo(host='127.0.0.1',port=port,user='nexol_app',password=settings['app_password'],dbname='nexol')

def stop_postgres(home,runtime):
    execute(pg_binary(runtime,'pg_ctl'),['-D',Path(home)/'postgres-data','-m','fast','-w','stop'],home)

def transfer_database(home,runtime,file,restore=False):
    """Portable logical backup: passwords never appear in command-line arguments."""
    home=Path(home).resolve();runtime=Path(runtime).resolve();file=Path(file).resolve()
    settings=secure_store.read(home/'postgres.sec')
    env=os.environ.copy();env['PGPASSWORD']=settings['admin_password']
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    tool=pg_binary(runtime,'pg_restore' if restore else 'pg_dump')
    args=['-h','127.0.0.1','-p',str(settings['port']),'-U','nexol_admin','-d','nexol']
    if restore:args.extend(['--exit-on-error','--no-owner','--no-privileges','--role=nexol_app',str(file)])
    else:args.extend(['--format=custom','--no-owner','--no-privileges','--file',str(file)])
    result=subprocess.run([str(tool),*args],env=env,stdin=subprocess.DEVNULL,capture_output=True,timeout=180,creationflags=flags)
    if result.returncode:raise RuntimeError('PostgreSQL backup: '+result.stderr.decode('utf-8','replace')[-1500:])

def restore_initial(home,runtime,file,dsn):
    """Restore only into a new, empty database. Existing host data is never replaced."""
    file=Path(file)
    if not file.exists():return
    with psycopg.connect(dsn) as db:
        exists=db.execute("SELECT to_regclass('public.employees')").fetchone()[0]
    if exists:return
    transfer_database(home,runtime,file,restore=True)
