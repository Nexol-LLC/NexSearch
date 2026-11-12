"""Start the existing local host during the Rust migration; never clears its DB."""
from pathlib import Path
import json, os, sys

ROOT=Path(__file__).resolve().parents[2]
HOME=ROOT/'preview/host-data'
sys.path.insert(0,str(ROOT))
os.environ.update(NEXOL_HOME=str(HOME),NEXSEARCH_KEY_FILE=str(HOME/'identity/host-key.pem'),NEXSEARCH_SECRET_FILE=str(HOME/'host-secrets.enc'))
from network.tls import identity
from network.postgres_runtime import start_postgres
from network.database import Database
from network.api import create_app
from network.launcher import admin_session
from network.broadcast import HostBroadcast
from network.discovery import HostDiscovery
import uvicorn

def main():
    runtime=ROOT/'preview/HostWindows/postgres'
    if not (runtime/'bin/pg_ctl.exe').exists():runtime=ROOT.parent/'NexSearch-1.6.6'/'Хост Windows'/'postgres'
    trust,cert,key=identity(HOME/'identity');os.environ['NEXOL_HOST_ID']=trust['host_id']
    db=Database(start_postgres(HOME,runtime,54357))
    app=create_app(db,web=ROOT/'network/web',pack=ROOT/'pack')
    admin_session(db,HOME)
    config={'endpoint':'https://127.0.0.1:8743','trust':trust,'import_session':str(HOME/'admin.sec')}
    target=ROOT/'next/local-data';target.mkdir(exist_ok=True)
    (target/'connection.json').write_text(json.dumps(config),encoding='utf-8')
    with db.transaction(admin=True) as c:
        totals={name:c.execute('SELECT count(*) AS n FROM '+name).fetchone()['n'] for name in ('employees','companies','employee_clients','carriers')}
    (target/'host-ready.json').write_text(json.dumps({'port':8743,'identity':trust['host_id'],'counts':totals,'database_cleared':False}),encoding='utf-8')
    broadcast=HostBroadcast(trust,8743);discovery=HostDiscovery(trust,8743,db)
    try:uvicorn.run(app,host='127.0.0.1',port=8743,ssl_certfile=str(cert),ssl_keyfile=str(key),access_log=False)
    finally:discovery.close();broadcast.close();db.close()

if __name__=='__main__':main()
