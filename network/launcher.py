"""Portable host and employee entry points. Runtime data never goes in _MEIPASS."""
from pathlib import Path
import json
import logging
import os
import secrets
import sys
import threading
import time
import webbrowser
from .desktop import open_window
from uuid import uuid4

def resources():
    if os.environ.get('NEXOL_RESOURCES'):return Path(os.environ['NEXOL_RESOURCES']).resolve()
    return Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))

def program_dir():
    if os.environ.get('NEXOL_PROGRAM_DIR'):return Path(os.environ['NEXOL_PROGRAM_DIR']).resolve()
    return Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]

def home_for(kind,trust=None):
    if os.environ.get('NEXOL_HOME'):return Path(os.environ['NEXOL_HOME']).resolve()
    if kind=='host':return program_dir()/'Данные хоста'
    if sys.platform=='darwin':return Path.home()/'Library/Application Support/NexSearch/Employees'/trust['host_id']
    return Path(os.environ.get('APPDATA',Path.home()/'.local/share'))/'Nexol'/trust['host_id']

class Instance:
    def __init__(self,home):
        home.mkdir(parents=True,exist_ok=True);self.url_file=home/'interface.json'
        self.file=(home/'instance.lock').open('a+b');self.file.seek(0);self.file.write(b'0');self.file.flush();self.file.seek(0)
    def acquire(self):
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            return True
        except (OSError,BlockingIOError):
            try:
                if not os.environ.get('NEXOL_NO_BROWSER'):webbrowser.open(json.loads(self.url_file.read_text())['url'])
            except (OSError,ValueError):pass
            return False
    def ready(self,port):
        self.url_file.write_text(json.dumps({'url':f'http://127.0.0.1:{port}'}),encoding='utf-8')
    def close(self):self.file.close()

def configure(home):
    home.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(filename=home/'program.log',level=logging.INFO,encoding='utf-8',format='%(asctime)s %(levelname)s %(name)s %(message)s')

def show_error(home):
    logging.exception('Application failed')
    if os.name=='nt' and not os.environ.get('NEXOL_NO_BROWSER'):
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,f'Не удалось запустить программу. Подробности: {home / "program.log"}', 'Nexol',0x10)

def admin_session(db,home):
    from .auth import Authentication,HASHER
    from . import secure_store
    local=secure_store.read(home/'admin.sec',{})
    device=local.setdefault('device_id','host-'+str(uuid4()))
    with db.transaction(admin=True) as conn:
        row=conn.execute("SELECT * FROM employees WHERE is_admin ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            row=conn.execute("INSERT INTO employees(id,login,first_name,last_name,password_hash,is_admin,role,requested_role) VALUES(%s,'host','Хост','Администратор',%s,true,'director','director') RETURNING *",(uuid4(),HASHER.hash(secrets.token_urlsafe(32)))).fetchone()
        conn.execute('DELETE FROM sessions WHERE device_id=%s',(device,))
    result=Authentication(db).issue(row,device,'Хост','127.0.0.1')
    local.update(token=result['token'],profile=result['employee']);secure_store.write(home/'admin.sec',local)
    local=json.loads(json.dumps(local,default=str))
    return local

def run_host():
    from .tls import identity
    from .postgres_runtime import start_postgres,stop_postgres,restore_initial
    from .database import Database
    from .api import create_app
    from .client import start_client
    from .discovery import HostDiscovery
    import uvicorn
    home=home_for('host');configure(home);instance=Instance(home)
    if not instance.acquire():return
    db=None;server=None;bridge_close=None;discovery=None;pg_started=False;runtime=Path(os.environ.get('NEXOL_POSTGRES_RUNTIME',program_dir()/'postgres'))
    try:
        bundle=resources()/'network/identity'
        if not bundle.exists():bundle=program_dir()/'network/identity'
        config=home/'identity';config.mkdir(exist_ok=True)
        if not (config/'host-identity.json').exists() and bundle.exists():
            import shutil
            for name in ('host-cert.pem','host-key.pem','host-identity.json'):shutil.copy2(bundle/name,config/name)
        trust,cert,key=identity(config)
        (program_dir()/'client-trust.json').write_text(json.dumps(trust),encoding='utf-8')
        os.environ['NEXOL_HOST_ID']=trust['host_id']
        dsn=os.environ.get('NEXOL_DATABASE_URL')
        if not dsn:
            if not runtime.exists():runtime=resources()/'network/.runtime/postgres'
            dsn=start_postgres(home,runtime,int(os.environ.get('NEXOL_POSTGRES_PORT','54341')));pg_started=True
            # Version 1.3 starts with a clean database; importing old snapshots is explicit.
        os.environ['NEXOL_HOME']=str(home);os.environ['NEXOL_POSTGRES_RUNTIME']=str(runtime)
        db=Database(dsn)
        pack=resources()/'pack'
        if not pack.exists():pack=program_dir().parent/'PackOnTruck'
        os.environ['NEXSEARCH_KEY_FILE']=str(key)
        os.environ['NEXSEARCH_SECRET_FILE']=str(home/'host-secrets.enc')
        seed=bundle/'host-secrets.enc'
        if seed.exists() and not (home/'host-secrets.enc').exists():
            import shutil
            shutil.copy2(seed,home/'host-secrets.enc')
        app=create_app(db,web=resources()/'network/web',pack=pack)
        port=int(os.environ.get('NEXOL_HOST_PORT','8743'))
        server=uvicorn.Server(uvicorn.Config(app,host='0.0.0.0',port=port,ssl_certfile=str(cert),ssl_keyfile=str(key),log_config=None,access_log=False))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        for _ in range(300):
            if server.started:break
            if not thread.is_alive():raise RuntimeError('Не удалось открыть порт хоста')
            time.sleep(.1)
        else:raise RuntimeError('Хост не запустился за 30 секунд')
        discovery=HostDiscovery(trust,port,db)
        profile=admin_session(db,home);profile['host_url']=f'https://127.0.0.1:{port}'
        bridge,agent,bridge_close=start_client(trust,home/'interface',resources()/'network/web',port=int(os.environ.get('NEXOL_UI_PORT','8765')),open_browser=False,profile=profile,discovery=False)
        instance.ready(bridge.server_port);open_window(f'http://127.0.0.1:{bridge.server_port}/',home,program_dir(),'host');bridge.serve_forever()
    except Exception:show_error(home);raise
    finally:
        if discovery:discovery.close()
        if server:
            server.should_exit=True
            thread.join(timeout=60)
        if bridge_close:bridge_close()
        if db:db.close()
        if pg_started:stop_postgres(home,runtime)
        instance.close()

def run_employee():
    from .client import start_client
    trust_file=program_dir()/'client-trust.json'
    if not trust_file.exists():trust_file=resources()/'network/client-trust.json'
    trust=json.loads(trust_file.read_text(encoding='utf-8'))
    from .install_client import launch_installed
    if launch_installed(program_dir(),trust):return
    home=home_for('employee',trust);configure(home);instance=Instance(home)
    if not instance.acquire():return
    close=None;updater=None
    try:
        server,agent,close=start_client(trust,home,resources()/'network/web',port=int(os.environ.get('NEXOL_UI_PORT','8765')),open_browser=False)
        from .daily_updates import DailyUpdater,install_schedule
        install_schedule(program_dir(),home,trust)
        updater=agent.updater=DailyUpdater(agent,program_dir())
        agent.legacy_paths=[program_dir()/'data/companies.sqlite3',program_dir()/'data/companies.db']
        if agent.state.get('token'):threading.Thread(target=agent.import_legacy,daemon=True).start()
        instance.ready(server.server_port);open_window(f'http://127.0.0.1:{server.server_port}/',home,program_dir());server.serve_forever()
    except Exception:show_error(home);raise
    finally:
        if updater:updater.close()
        if close:close()
        instance.close()
        from .daily_updates import apply_pending_on_exit
        apply_pending_on_exit(program_dir(),home)
