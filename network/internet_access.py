"""Public relay address only. TLS and employee credentials stay with the office host."""
from pathlib import Path
from urllib.parse import urlsplit
import ipaddress,json,re,threading,time

class ProfileError(ValueError):pass

def public_url(value):
    if not isinstance(value,str) or any(ord(c)<33 for c in value):raise ProfileError('Некорректный адрес интернет-шлюза')
    try:
        p=urlsplit(value);port=p.port
        if port is not None and not 1<=port<=65535:raise ValueError()
        if p.scheme!='https' or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ('','/'):raise ValueError()
        host=p.hostname.encode('idna').decode('ascii').lower()
        try:
            if not ipaddress.ip_address(host).is_global:raise ProfileError('Для шлюза нужен публичный адрес')
        except ValueError as error:
            if isinstance(error,ProfileError):raise
            if '.' not in host or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',host) or any(not label or len(label)>63 for label in host.split('.')):raise ValueError()
        return 'https://'+('['+host+']' if ':' in host else host)+(f':{port}' if port and port!=443 else '')
    except (ValueError,UnicodeError):raise ProfileError('Нужен постоянный HTTPS-адрес публичного шлюза без пути, логина и пароля') from None

def validate_profile(value,trust):
    if not isinstance(value,dict) or set(value)!={'version','host_id','fingerprint','url'} or value.get('version')!=1:raise ProfileError('Файл подключения имеет неподдерживаемый формат')
    if value['host_id']!=trust['host_id'] or value['fingerprint']!=trust['fingerprint']:raise ProfileError('Файл подключения предназначен для другого хоста')
    return {**value,'url':public_url(value['url'])}

def load_profile(program,trust,resource=None):
    # A deliberately present invalid override never falls through to another host.
    paths=[Path(program)/'internet-access.json']
    if resource:paths.extend([Path(resource)/'network/internet-access.json',Path(resource)/'internet-access.json'])
    for path in paths:
        if path.is_file():
            try:return validate_profile(json.loads(path.read_text(encoding='utf-8')),trust),''
            except (ValueError,OSError,TypeError):return None,'Файл интернет-подключения не прошёл проверку. Обратитесь к администратору.'
    return None,''

def offline_candidates(state,profile):
    mode=state.get('connection_preference','auto')
    remote=profile['url'] if profile else None
    lan=state.get('host_url')
    vpn=state.get('active_host_url') if state.get('active_transport')=='vpn' else None
    if mode=='internet':return [remote] if remote else []
    if mode=='lan':return [lan] if lan else []
    if mode=='vpn':return [vpn] if vpn else []
    return list(dict.fromkeys(u for u in (lan,remote,vpn) if u))

def healthy(session,url,host_id,timeout=2):
    import requests
    try:
        response=session.get(url+'/api/host-info',timeout=(timeout,timeout),allow_redirects=False)
        return response.status_code==200 and response.json().get('host_id')==host_id
    except (requests.RequestException,ValueError,AttributeError):return False

class InternetMonitor:
    """Bounded probes, separate pinned session, reconnect backoff; never retries writes."""
    def __init__(self,agent,interval=5):
        import requests
        self.agent=agent;self.interval=interval;self.closed=threading.Event();self.wake=threading.Event();self.thread=None
        self.session=requests.Session();self.session.trust_env=False
        from .client import PinnedAdapter
        self.session.mount('https://',PinnedAdapter(agent.trust['fingerprint']))
    def start(self):
        self.thread=threading.Thread(target=self.run,name='internet-connection',daemon=True);self.thread.start()
    def run(self):
        failures=0;lan_failures=0;next_remote=0.
        while not self.closed.is_set():
            try:
                with self.agent.lock:lan=self.agent.lan_url;remote=self.agent.internet_url;mode=self.agent.preference
                if lan and mode in ('auto','lan'):
                    good=healthy(self.session,lan,self.agent.trust['host_id'])
                    lan_failures=0 if good else lan_failures+1
                    if good or lan_failures>=2:self.agent.lan_health(lan,good)
                if remote and mode in ('auto','internet') and time.monotonic()>=next_remote:
                    good=healthy(self.session,remote,self.agent.trust['host_id'])
                    self.agent.internet_health(remote,good);failures=0 if good else min(failures+1,4)
                    next_remote=time.monotonic()+(self.interval if good else min(30,2**failures))
            except Exception:
                __import__('logging').getLogger(__name__).exception('Connection health probe failed')
            if self.wake.wait(self.interval):next_remote=0
            self.wake.clear()
    def nudge(self):self.wake.set()
    def close(self):
        self.closed.set();self.wake.set()
        if self.thread:self.thread.join(timeout=5)
        self.session.close()
