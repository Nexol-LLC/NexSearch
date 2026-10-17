"""Headless Ubuntu host. PostgreSQL is managed by the operating system."""
from pathlib import Path
import os,signal,logging
import uvicorn
from .database import Database
from .api16 import create_app
from .tls import identity
from .discovery import HostDiscovery
from .broadcast import HostBroadcast

def main():
 root=Path(__file__).resolve().parents[1];home=Path(os.environ.get('NEXOL_HOME','/var/lib/nexsearch'))
 logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
 trust,cert,key=identity(home/'identity');os.environ['NEXOL_HOST_ID']=trust['host_id']
 os.environ['NEXSEARCH_KEY_FILE']=str(key);os.environ['NEXSEARCH_SECRET_FILE']=str(home/'host-secrets.enc')
 db=Database(os.environ['NEXOL_DATABASE_URL']);app=create_app(db,web=root/'network/web',pack=root/'pack')
 port=int(os.environ.get('NEXOL_HOST_PORT','8743'));discovery=HostDiscovery(trust,port,db);broadcast=HostBroadcast(trust,port)
 from .remote_access import RemoteAccess
 remote=app.state.remote_access=RemoteAccess(home/'vpn-host',trust,mode='host',port=port);remote.start()
 try:uvicorn.run(app,host='0.0.0.0',port=port,ssl_certfile=str(cert),ssl_keyfile=str(key),access_log=False)
 finally:remote.close();broadcast.close();discovery.close();db.close()
if __name__=='__main__':main()
