"""Supervise a private, application-only tsnet helper. No VPN admin key in clients."""
from pathlib import Path
from urllib.parse import urlsplit
import hashlib,json,os,platform,re,shutil,subprocess,sys,threading,time
from . import secure_store

CONTROL='https://controlplane.tailscale.com'
MESSAGES={
 'disabled':'Удалённое подключение выключено', 'missing':'Сетевой модуль не входит в этот комплект',
 'starting':'Запускаем защищённое подключение…','connecting':'Подключаемся к VPN-сети…',
 'needs_login':'Требуется однократный вход в вашу VPN-сеть',
 'needs_approval':'Устройство ожидает подтверждения администратором VPN-сети',
 'host_offline':'VPN подключён, офисный хост пока не найден в вашей сети',
 'online':'VPN подключён', 'error':'Не удалось запустить VPN-модуль',
 'reconnecting':'Восстанавливаем VPN-соединение…'}

def host_name(trust):return 'nexsearch-'+re.sub('[^a-z0-9]','',trust['host_id'].lower())[:16]

def validate_config(value):
    if not isinstance(value,dict) or set(value)-{'enabled','provider','control_url'}:raise ValueError('Неизвестные настройки VPN')
    if type(value.get('enabled')) is not bool:raise ValueError('Укажите состояние VPN')
    provider=value.get('provider','tailscale')
    if provider not in ('tailscale','headscale'):raise ValueError('Неизвестный VPN-провайдер')
    control=CONTROL if provider=='tailscale' else str(value.get('control_url','')).rstrip('/')
    parsed=urlsplit(control)
    try:port=parsed.port
    except ValueError:raise ValueError('Проверьте адрес Headscale') from None
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/') or any(ord(c)<33 for c in control):
        raise ValueError('Укажите HTTPS-адрес сервера Headscale без логина, пароля и дополнительных параметров')
    return {'enabled':value['enabled'],'provider':provider,'control_url':control}

def helper_path():
    if os.environ.get('NEXSEARCH_VPN_HELPER'):return Path(os.environ['NEXSEARCH_VPN_HELPER'])
    machine='arm64' if platform.machine().lower() in ('arm64','aarch64') else 'amd64'
    system='windows' if os.name=='nt' else 'darwin' if sys.platform=='darwin' else 'linux'
    root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))
    name='nexsearch-vpn.exe' if os.name=='nt' else 'nexsearch-vpn'
    return root/'network/bin'/f'{system}-{machine}'/name

class RemoteAccess:
    def __init__(self,home,trust,mode='employee',device='',port=8743,on_remote=None):
        self.home=Path(home);self.home.mkdir(parents=True,exist_ok=True)
        self.path=self.home/'vpn-config.sec';self.trust=trust;self.mode=mode;self.port=port
        self.device=device;self.on_remote=on_remote or (lambda url:None)
        self.config=secure_store.read(self.path,{'enabled':False,'provider':'tailscale','control_url':CONTROL})
        self.config=validate_config(self.config)
        self.lock=threading.RLock();self.operation_lock=threading.RLock();self.thread=None;self.process=None;self.closed=threading.Event()
        self.event={'phase':'disabled'};self.last_event=0.
    def status(self):
        with self.lock:
            result={**self.event,'config':dict(self.config),'host_name':host_name(self.trust),'mode':self.mode}
            if not self.config['enabled']:result['phase']='disabled'
            elif result['phase']=='online' and time.monotonic()-self.last_event>20:result['phase']='reconnecting'
            result['message']=MESSAGES.get(result['phase'],MESSAGES['connecting'])
            return result
    def _event(self,value):
        # Only known status fields cross the helper/UI boundary.
        if not isinstance(value,dict):raise ValueError('Invalid VPN status')
        phase=value.get('phase','error')
        if phase not in MESSAGES:phase='error'
        data={'phase':phase}
        if value.get('address'):data['address']=str(value['address'])[:100]
        if value.get('auth_url'):
            parsed=urlsplit(str(value['auth_url']))
            expected=urlsplit(self.config['control_url']).hostname
            allowed={expected,'login.tailscale.com'} if self.config['provider']=='tailscale' else {expected}
            if parsed.scheme=='https' and parsed.hostname in allowed and not parsed.username and not parsed.password:data['auth_url']=value['auth_url'][:3000]
        local=value.get('local_url')
        if local:
            parsed=urlsplit(str(local))
            try:valid=parsed.scheme=='https' and parsed.hostname=='127.0.0.1' and 1024<=parsed.port<=65535 and parsed.path in ('','/') and not parsed.query and not parsed.fragment and not parsed.username
            except (ValueError,TypeError):valid=False
            if not valid:data={'phase':'error'};local=None
        with self.lock:self.event=data;self.last_event=time.monotonic()
        if self.mode=='employee':self.on_remote(local if phase=='online' and data['phase']=='online' else None)
    def start(self):
        with self.lock:
            if not self.config['enabled'] or self.thread and self.thread.is_alive():return
            self.closed.clear();self.thread=threading.Thread(target=self.run,name='nexsearch-vpn',daemon=True);self.thread.start()
    def configure(self,value):
        config=validate_config(value)
        with self.operation_lock:
            self.close()
            with self.lock:
                self.config=config;secure_store.write(self.path,config);self.event={'phase':'disabled'}
            self.start();return self.status()
    def login(self):
        self.start()
        with self.lock:
            if self.process and self.process.poll() is None:
                try:self.process.stdin.write('login\n');self.process.stdin.flush()
                except (OSError,ValueError):pass
        return self.status()
    def run(self):
        binary=helper_path()
        if not binary.is_file():self._event({'phase':'missing'});return
        # Older signed updaters extracted every file as 0644. Do not require a
        # privileged repair: execute an exact private copy of the shipped helper.
        if os.name!='nt' and not os.access(binary,os.X_OK):
            try:
                digest=hashlib.sha256(binary.read_bytes()).hexdigest()
                folder=self.home/'bin'/digest;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
                executable=folder/'nexsearch-vpn';pending=folder/'pending'
                shutil.copyfile(binary,pending);pending.chmod(0o700);pending.replace(executable);binary=executable
            except OSError:self._event({'phase':'error'});return
        retry=2
        while not self.closed.is_set():
            self._event({'phase':'starting'})
            with self.lock:config=dict(self.config)
            directory=self.home/('state-'+hashlib.sha256(config['control_url'].encode()).hexdigest()[:12])
            directory.mkdir(exist_ok=True)
            if os.name!='nt':directory.chmod(0o700)
            hostname=host_name(self.trust) if self.mode=='host' else 'nexemployee-'+hashlib.sha256(self.device.encode()).hexdigest()[:16]
            arguments={'mode':self.mode,'dir':str(directory),'hostname':hostname,'target':host_name(self.trust),'control_url':config['control_url'],'port':self.port}
            env=os.environ.copy()
            for key in list(env):
                if key.startswith('TS_') or key in ('CHECKO_API_KEY','NEXOL_DATABASE_URL'):env.pop(key,None)
            env['TS_NO_LOGS_NO_SUPPORT']='1'
            process=None
            try:
                process=subprocess.Popen([str(binary)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                    text=True,encoding='utf-8',env=env,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                with self.lock:self.process=process
                process.stdin.write(json.dumps(arguments)+'\n');process.stdin.flush()
                for line in process.stdout:
                    if self.closed.is_set():break
                    if len(line)>65536:continue
                    try:self._event(json.loads(line))
                    except (ValueError,TypeError,KeyError):continue
                if process.poll() is None:process.wait(timeout=5)
            except (OSError,subprocess.TimeoutExpired):self._event({'phase':'error'})
            finally:
                if process and process.poll() is None:process.terminate()
                if process:
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
                with self.lock:
                    if self.process is process:self.process=None
            if self.closed.is_set():break
            self._event({'phase':'reconnecting'})
            self.closed.wait(retry);retry=min(30,retry*2)
    def close(self):
        self.closed.set()
        with self.lock:process=self.process;thread=self.thread
        if process and process.poll() is None:
            try:process.stdin.write('stop\n');process.stdin.flush()
            except (OSError,ValueError):pass
            try:process.wait(timeout=8)
            except subprocess.TimeoutExpired:process.terminate()
        if thread and thread is not threading.current_thread():thread.join(timeout=12)
        self.on_remote(None)
