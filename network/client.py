from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import json
import os
import secrets
import socket
import ssl
import threading
from uuid import uuid4
import webbrowser
from requests.adapters import HTTPAdapter
from urllib3 import PoolManager
import requests
from . import secure_store
from .discovery import EmployeeDiscovery,is_local

class ExclusiveHTTPServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR permits two unrelated apps to bind the same port.
    # Never share a listener with the old standalone application.
    allow_reuse_address=os.name!='nt'

class PinnedAdapter(HTTPAdapter):
    def __init__(self,fingerprint):
        self.fingerprint=fingerprint
        super().__init__()
    def init_poolmanager(self,connections,maxsize,block=False,**kwargs):
        self.poolmanager=PoolManager(num_pools=connections,maxsize=maxsize,block=block,
            cert_reqs=ssl.CERT_NONE,assert_fingerprint=self.fingerprint,**kwargs)
    def cert_verify(self,conn,url,verify,cert):
        # Trust this exact bundled host certificate, rather than the system CA set.
        conn.cert_reqs=ssl.CERT_NONE;conn.assert_fingerprint=self.fingerprint

class EmployeeAgent:
    def __init__(self,trust,home,profile=None,internet_profile=None,profile_error=''):
        self.trust=trust;self.home=Path(home);self.home.mkdir(parents=True,exist_ok=True)
        self.path=self.home/'employee.sec';self.state=secure_store.read(self.path,{})
        if profile:self.state.update(profile)
        self.state.setdefault('device_id',str(uuid4()));self.state.setdefault('computer_name',socket.gethostname())
        secure_store.write(self.path,self.state)
        self.csrf=secrets.token_urlsafe(32);self.lock=threading.Lock();self.url=self.state.get('host_url');self.discovery=None;self.legacy_paths=[];self.import_lock=threading.Lock()
        self.session=requests.Session();self.session.trust_env=False;self.session.mount('https://',PinnedAdapter(trust['fingerprint']))
        self.preference=self.state.get('connection_preference','auto')
        if self.preference not in ('auto','lan','vpn','internet'):self.preference='auto'
        self.lan_url=self.url;self.vpn_url=None;self.connection_mode='lan' if self.url else None;self.last_lan_probe=0.
        self.lan_online=bool(self.lan_url);self.vpn_online=False
        from .internet_access import validate_profile,InternetMonitor
        self.internet_profile=validate_profile(internet_profile,trust) if internet_profile else None
        self.internet_url=self.internet_profile['url'] if self.internet_profile else None;self.internet_online=False;self.internet_error=profile_error
        self.internet_checked_at=None
        if self.preference in ('vpn','internet'):self.url=None;self.connection_mode=None
        from .remote_access import RemoteAccess
        self.vpn=RemoteAccess(self.home/'vpn',trust,device=self.state['device_id'],on_remote=self.remote_found)
        self.internet=InternetMonitor(self)
    def _choose(self):
        choices={'lan':self.lan_url if self.lan_online else None,'internet':self.internet_url if self.internet_online else None,'vpn':self.vpn_url if self.vpn_online else None}
        order=('lan','internet','vpn') if self.preference=='auto' else (self.preference,)
        selected=next(((mode,choices[mode]) for mode in order if choices.get(mode)),(None,None))
        self.connection_mode,self.url=selected
        if self.state.get('active_host_url')!=self.url or self.state.get('active_transport')!=self.connection_mode:
            self.state.update(active_host_url=self.url,active_transport=self.connection_mode);secure_store.write(self.path,self.state)
    def transport(self):
        with self.lock:return dict(preference=self.preference,transport=self.connection_mode,host_url=self.url,host_found=bool(self.url),internet_configured=bool(self.internet_url),internet_online=self.internet_online,internet_url=self.internet_url,internet_error=self.internet_error,internet_checked_at=self.internet_checked_at)
    def select_transport(self,mode):
        if mode not in ('auto','lan','vpn','internet'):raise ValueError('Неизвестный способ подключения')
        with self.lock:
            was_internet=self.preference=='internet'
            self.preference=mode;self.state['connection_preference']=mode;secure_store.write(self.path,self.state)
            self._choose();lan=self.lan_url;vpn=self.vpn_url
        if mode=='internet':self.vpn.close()
        if mode=='vpn' and not self.vpn.config['enabled']:self.vpn.configure({**self.vpn.config,'enabled':True})
        elif mode=='vpn' and was_internet:self.vpn.start()
        if mode in ('auto','lan') and lan:self.found(lan)
        if mode in ('auto','vpn') and vpn:self.remote_found(vpn)
        self.internet.nudge()
        return self.transport()
    def lan_health(self,url,online):
        with self.lock:
            if self.lan_url!=url:return
            self.lan_online=online;self._choose()
    def internet_health(self,url,online):
        from datetime import datetime,timezone
        with self.lock:
            if self.internet_url!=url:return
            self.internet_online=online;self.internet_checked_at=datetime.now(timezone.utc).isoformat();self._choose()
    def remote_found(self,url):
        from .internet_access import healthy
        with self.lock:
            self.vpn_url=url;self.vpn_online=False;lan=self.lan_url;mode=self.preference
        if url and mode in ('auto','vpn'):
            if mode=='auto' and lan:self.lan_health(lan,healthy(self.session,lan,self.trust['host_id']))
            online=healthy(self.session,url,self.trust['host_id'])
            with self.lock:
                if self.vpn_url==url:self.vpn_online=online
        with self.lock:self._choose()
    def found(self,url):
        if not is_local(__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(url).hostname):return
        from .internet_access import healthy
        good=healthy(self.session,url,self.trust['host_id'])
        with self.lock:
            if good:
                self.lan_url=url;self.lan_online=True;self.state['host_url']=url;secure_store.write(self.path,self.state)
            elif self.lan_url==url:self.lan_online=False
            self._choose();token=self.state.get('token')
        if good and token:self.import_legacy()
    def request(self,path,method='GET',body=b'',content_type='application/json'):
        with self.lock:url=self.url;token=self.state.get('token')
        if not url:raise ConnectionError('Ожидаем хост по выбранному способу подключения. Проверьте состояние подключения.')
        headers={'Content-Type':content_type}
        if token:headers['Authorization']='Bearer '+token
        payload=None
        if path in ('/api/auth/register','/api/auth/login'):
            payload=json.loads(body);payload.update(device_id=self.state['device_id'],computer_name=self.state['computer_name']);body=json.dumps(payload).encode()
        try:response=self.session.request(method,url+path,data=body if method not in ('GET','HEAD') else None,headers=headers,timeout=(3,90),allow_redirects=False)
        except requests.RequestException:
            self.internet.nudge()
            raise  # A failed write may already have committed: never resend it automatically.
        if path in ('/api/auth/register','/api/auth/login') and response.ok:
            data=response.json()
            with self.lock:
                self.state.update(token=data.pop('token'),profile=data['employee']);secure_store.write(self.path,self.state)
            threading.Thread(target=self.import_legacy,daemon=True).start()
            return response.status_code,{'Content-Type':'application/json'},json.dumps(data,ensure_ascii=False).encode('utf-8')
        if path=='/api/auth/logout' and response.ok:
            with self.lock:self.state.pop('token',None);secure_store.write(self.path,self.state)
        # Starlette sends lower-case names. Preserve MIME types through the bridge
        # using the canonical keys consumed by Bridge.send.
        headers={'Content-Type':response.headers.get('content-type','application/octet-stream')}
        if 'content-disposition' in response.headers:headers['Content-Disposition']=response.headers['content-disposition']
        return response.status_code,headers,response.content

    def import_legacy(self):
        """Read the old base without modifying it. Mark complete only after host acknowledgement."""
        if not self.url or not self.state.get('token') or not self.import_lock.acquire(blocking=False):return
        try:
            import sqlite3
            for path in self.legacy_paths:
                path=Path(path).resolve();key='legacy:'+str(path)+':'+self.state['profile']['id']
                if not path.exists() or self.state.get(key):continue
                with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as old:
                    old.row_factory=sqlite3.Row
                    calls={r['company_id']:dict(r) for r in old.execute('SELECT * FROM company_calls')}
                    batch=[]
                    for row in old.execute('SELECT id,payload FROM companies'):
                        item=json.loads(row['payload']);item.update(calls.get(row['id'],{}));batch.append(item)
                        if len(batch)==200:
                            self._import_batch(batch);batch=[]
                    if batch:self._import_batch(batch)
                with self.lock:self.state[key]=True;secure_store.write(self.path,self.state)
        except Exception:
            __import__('logging').getLogger('nexol.import').exception('Old base transfer will be retried')
        finally:self.import_lock.release()

    def _import_batch(self,companies):
        code,_,_=self.request('/api/import/legacy','POST',json.dumps({'companies':companies}).encode())
        if code!=200:raise ConnectionError('Хост не подтвердил перенос базы')

def start_client(trust,home,resources,port=8765,open_browser=True,profile=None,discovery=True,internet_profile=None,profile_error=''):
    agent=EmployeeAgent(trust,home,profile,internet_profile,profile_error)
    class Invitation(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            try:
                size=int(self.headers.get('Content-Length','0'))
                if self.path!='/nexol/connect' or size<1 or size>512:raise ValueError()
                data=json.loads(self.rfile.read(size))
                if data.get('host_id')!=trust['host_id'] or data.get('fingerprint')!=trust['fingerprint']:raise ValueError()
                host_port=int(data['port'])
                if not 1024<=host_port<=65535:raise ValueError()
                threading.Thread(target=agent.found,args=(f'https://{self.client_address[0]}:{host_port}',),daemon=True).start()
                self.send_response(204);self.end_headers()
            except (ValueError,KeyError,TypeError):self.send_response(400);self.end_headers()
    callback=ExclusiveHTTPServer(('0.0.0.0',0),Invitation)
    threading.Thread(target=callback.serve_forever,daemon=True).start()
    class Bridge(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def send(self,data,status=200,headers=None):
            if isinstance(data,dict):data=json.dumps(data,ensure_ascii=False,default=str).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type',(headers or {}).get('Content-Type','application/json; charset=utf-8'))
            self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            if headers and 'Content-Disposition' in headers:self.send_header('Content-Disposition',headers['Content-Disposition'])
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; frame-src 'self'; frame-ancestors 'self'; connect-src 'self'; base-uri 'none'")
            self.end_headers();self.wfile.write(data)
        def valid(self):return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
        def do_GET(self):
            if not self.valid():return self.send({'error':'Недопустимый адрес'},403)
            path=__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(self.path).path
            if path=='/api/events':return self.stream_events()
            if path=='/api/connection':return self.send(dict(**agent.transport(),csrf=agent.csrf,profile=agent.state.get('profile'),computer=agent.state['computer_name']))
            if path=='/api/client/transport':return self.send(agent.transport())
            if path=='/api/client/vpn/status':return self.send(agent.vpn.status())
            if path=='/api/client/update/status':
                from .daily_updates import local_status
                updater=getattr(agent,'updater',None)
                program=updater.program if updater else Path(resources).parent.parent
                return self.send({**local_status(program,agent.home),'supports_restart':bool(updater)})
            # The login screen is local so an absent host never produces a blank page.
            base=Path(resources)
            if path=='/':return self.send((base/'index.html').read_bytes(),headers={'Content-Type':'text/html; charset=utf-8'})
            if path.startswith('/assets/'):
                relative=__import__('urllib.parse',fromlist=['unquote']).unquote(path[len('/assets/'):])
                file=(base/relative).resolve()
                if not file.is_relative_to(base.resolve()) or not file.is_file():return self.send({'error':'Файл не найден'},404)
                import mimetypes
                content_type=mimetypes.guess_type(str(file))[0] or 'application/octet-stream'
                return self.send(file.read_bytes(),headers={'Content-Type':content_type})
            try:
                code,headers,data=agent.request(self.path)
                return self.send(data,code,headers)
            except (requests.RequestException,ConnectionError):return self.send({'error':'Хост недоступен. Данные останутся на хосте; ожидаем соединения.'},503)
        def stream_events(self):
            with agent.lock:host_url=agent.url;token=agent.state.get('token')
            if not host_url or not token:return self.send({'error':'Требуется вход'},401)
            try:
                headers={'Authorization':'Bearer '+token,'Last-Event-ID':self.headers.get('Last-Event-ID','0')}
                with agent.session.get(host_url+'/api/events',headers=headers,stream=True,timeout=(3,20),allow_redirects=False) as response:
                    if response.status_code!=200:return self.send({'error':'Нет доступа к событиям'},response.status_code)
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-store');self.end_headers()
                    for line in response.iter_lines(chunk_size=1):
                        self.wfile.write(line+b'\n');self.wfile.flush()
            except (requests.RequestException,BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        def mutation(self):
            if not self.valid() or self.headers.get('Origin') not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}') or not secrets.compare_digest(self.headers.get('X-Nexol-CSRF',''),agent.csrf):return self.send({'error':'Запрос отклонён'},403)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length<0 or length>5*1024*1024:return self.send({'error':'Слишком большой запрос'},413)
                body=self.rfile.read(length)
                if self.path=='/api/client/transport':return self.send(agent.select_transport(json.loads(body)['mode']))
                if self.path=='/api/client/internet/check':
                    agent.internet.nudge();return self.send(agent.transport())
                if self.path=='/api/client/vpn/config':return self.send(agent.vpn.configure(json.loads(body)))
                if self.path=='/api/client/vpn/login':return self.send(agent.vpn.login())
                if self.path=='/api/client/update/check':
                    updater=getattr(agent,'updater',None)
                    if updater:threading.Thread(target=updater.check,args=(True,),daemon=True).start()
                    return self.send({'queued':bool(updater)})
                if self.path=='/api/client/update/install':
                    updater=getattr(agent,'updater',None)
                    if not updater:return self.send({'error':'Откройте программу сотрудника'},400)
                    from .daily_updates import request_restart
                    return self.send(request_restart(updater.program,agent.home))
                if self.path=='/api/client/exit':
                    try:agent.request('/api/activity','POST',json.dumps({'kind':'session.exit','details':{}}).encode())
                    except Exception:pass
                    self.send({'ok':True});threading.Thread(target=self.server.shutdown,daemon=True).start();return
                code,headers,data=agent.request(self.path,self.command,body,self.headers.get('Content-Type','application/json'))
                self.send(data,code,headers)
            except (ValueError,KeyError):self.send({'error':'Некорректный запрос'},400)
            except (requests.RequestException,ConnectionError):self.send({'error':'Хост недоступен. Изменения не сохранены, текст остаётся в поле.'},503)
        do_POST=do_PATCH=do_PUT=do_DELETE=mutation
    server=None
    for selected in range(port,port+20):
        try:server=ExclusiveHTTPServer(('127.0.0.1',selected),Bridge);break
        except OSError:continue
    if not server:raise RuntimeError('Не удалось открыть локальный интерфейс')
    if discovery:
        agent.discovery=EmployeeDiscovery(trust,agent.state['device_id'],agent.state['computer_name'],callback.server_port,agent.found)
        from .broadcast import EmployeeBroadcast
        agent.broadcast=EmployeeBroadcast(trust,agent.found)
    if agent.url:threading.Thread(target=agent.found,args=(agent.url,),daemon=True).start()
    if agent.preference!='internet':agent.vpn.start()
    agent.internet.start()
    if open_browser:webbrowser.open(f'http://127.0.0.1:{server.server_port}')
    def close():
        agent.internet.close();agent.vpn.close()
        server.shutdown();server.server_close();callback.shutdown();callback.server_close()
        if agent.discovery:agent.discovery.close()
        if getattr(agent,'broadcast',None):agent.broadcast.close()
        agent.session.close()
    return server,agent,close

def time_since(moment):return __import__('time').monotonic()-moment
