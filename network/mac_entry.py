"""Native macOS launcher after offline runtimes have been unpacked."""
from pathlib import Path
import json,os,sys,traceback

def native_schedule(base,home,host_id):
    """Keep the existing daily LaunchAgent, but use the native protected session.

    Otherwise a later login/logout in Rust would leave the old Python agent
    checking updates with the obsolete token in employee.sec.
    """
    import plistlib,subprocess,tempfile
    from uuid import UUID
    UUID(host_id)
    agent=Path.home()/'Library/LaunchAgents'/('com.nexsearch.updates.'+host_id+'.plist')
    if not agent.is_file():return
    try:
        with agent.open('rb') as stream:settings=plistlib.load(stream)
        if settings.get('Label')!='com.nexsearch.updates.'+host_id:return
        arguments=[str(base/'NexSearch Maintenance'),'check-daily','--program',str(base),'--home',str(home)]
        if settings.get('ProgramArguments')==arguments:return
        settings['ProgramArguments']=arguments;settings['WorkingDirectory']=str(base)
        # Preserve the established calendar interval and private log locations.
        with tempfile.NamedTemporaryFile(dir=agent.parent,delete=False) as stream:
            temporary=Path(stream.name);plistlib.dump(settings,stream);stream.flush();os.fsync(stream.fileno())
        temporary.chmod(0o600);temporary.replace(agent)
        domain='gui/'+str(os.getuid());label=settings['Label']
        for command in (['/bin/launchctl','bootout',domain+'/'+label],['/bin/launchctl','bootstrap',domain,str(agent)]):
            subprocess.run(command,check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):
        # The written agent also loads on the next login. A scheduler failure
        # must never prevent opening the application or its in-app updater.
        pass

def main():
    role=sys.argv[1]
    if role not in ('host','employee'):raise ValueError('Unknown program role')
    base=Path(__file__).resolve().parents[1]
    metadata=base/'package-info.json'
    if role=='employee' and metadata.is_file() and json.loads(metadata.read_text(encoding='utf-8')).get('runtime')=='rust':
        # The 1.6.7 updater restarts this entry once. Replace it with the signed
        # native executable; the old Python runtime never serves the new UI.
        for name in ('NexSearch','NexSearch Maintenance'):(base/name).chmod(0o700)
        public=json.loads((base/'network/client-trust.json').read_text(encoding='utf-8'))
        home=Path.home()/'Library/Application Support/NexSearch/Employees'/public['host_id']
        native_schedule(base,home,public['host_id'])
        if (home/'employee.sec').is_file() and not (home/'native-migration.complete').exists():
            # Read with the already trusted old Keychain identity, then hand off
            # through an anonymous pipe. No token in argv, environment or a file.
            from network import secure_store
            saved=secure_store.read(home/'employee.sec',{})
            payload={k:saved[k] for k in ('token','device_id','host_url','active_host_url','connection_preference') if isinstance(saved.get(k),str)}
            raw=json.dumps(payload).encode('utf-8')
            if len(raw)>4096:raise ValueError('Invalid legacy handoff size')
            reader,writer=os.pipe()
            try:os.write(writer,raw)
            finally:os.close(writer)
            os.dup2(reader,0);os.close(reader);os.environ['NEXSEARCH_LEGACY_HANDOFF']='1'
        os.execv(str(base/'NexSearch'),[str(base/'NexSearch')])
    os.environ['NEXOL_RESOURCES']=str(base)
    os.environ['NEXOL_PROGRAM_DIR']=str(base)
    public=json.loads((base/'network/client-trust.json').read_text(encoding='utf-8'))
    storage=Path.home()/'Library/Application Support/NexSearch'
    if role=='host':os.environ['NEXOL_HOME']=str(storage/'Hosts'/public['host_id'])
    else:os.environ['NEXOL_HOME']=str(storage/'Employees'/public['host_id'])
    home=Path(os.environ['NEXOL_HOME']);home.mkdir(parents=True,exist_ok=True);home.chmod(0o700)
    from network.launcher import run_host,run_employee
    (run_host if role=='host' else run_employee)()

if __name__=='__main__':
    try:main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
