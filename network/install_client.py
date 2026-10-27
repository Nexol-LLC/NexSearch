"""Copy a portable employee distribution to a stable per-user location once."""
from pathlib import Path
import os,sys,json,shutil,subprocess

def upgrade_existing(program,target,home):
    """A fresh full kit must upgrade an older installed copy, not silently reopen it."""
    from .daily_updates import older_package,write_state,read_state
    incoming=json.loads((program/'package-info.json').read_text(encoding='utf-8'))
    try:installed=json.loads((target/'package-info.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):installed={'version':'0'}
    if incoming.get('source_commit')==installed.get('source_commit') or older_package(installed,incoming):return False
    from .launcher import Instance
    previous=os.environ.get('NEXOL_NO_BROWSER');os.environ['NEXOL_NO_BROWSER']='1'
    instance=Instance(home)
    try:free=instance.acquire()
    finally:
        if previous is None:os.environ.pop('NEXOL_NO_BROWSER',None)
        else:os.environ['NEXOL_NO_BROWSER']=previous
    if not free:
        instance.close()
        if not os.environ.get('NEXOL_NO_BROWSER'):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,'Сначала полностью закройте прежнюю программу NexSearch, затем запустите новый комплект ещё раз.','Обновление NexSearch',0x40)
        return True
    try:
        import hashlib,zipfile
        from uuid import uuid4
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from .updates import prepare,safe_target
        proof=program.parent/'UpdateProof';raw=(proof/'release.json').read_bytes();signature=(proof/'release.sig').read_bytes()
        load_pem_public_key((target/'updates-public.pem').read_bytes()).verify(signature,raw)
        manifest=json.loads(raw)
        if manifest['role']!='EmployeeWindows' or manifest['source_commit']!=incoming['source_commit'] or manifest['version']!=incoming['version'] or older_package(installed,manifest):raise ValueError('Неверный полный комплект обновления')
        prefix='EmployeeWindows/'
        for name,digest in manifest['files'].items():
            if not name.startswith(prefix) or hashlib.sha256(safe_target(program,name[len(prefix):]).read_bytes()).hexdigest()!=digest:raise ValueError('Контрольная сумма полного комплекта не совпала')
        folder=home/'updates';folder.mkdir(parents=True,exist_ok=True);archive=folder/('full-kit-'+uuid4().hex+'.zip')
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_STORED) as z:
            z.writestr('release.json',raw);z.writestr('release.sig',signature)
            for name in manifest['files']:z.write(safe_target(program,name[len(prefix):]),name)
        prepared=prepare(archive,target,home,'EmployeeWindows')
        state=read_state(home);state.update(phase='ready',pending_state=prepared['state_file'],pending_commit=manifest['source_commit']);write_state(home,state)
        runner=folder/('bootstrap-'+uuid4().hex+'.exe');shutil.copy2(program/'NexSearch Maintenance.exe',runner)
        subprocess.Popen([str(runner),'apply','--state',prepared['state_file'],'--home',str(home),'--wait-pid',str(os.getpid()),'--restart'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS)
        return True
    finally:instance.close()

def launch_installed(program,trust):
    if sys.platform!='win32' or not getattr(sys,'frozen',False) or os.environ.get('NEXSEARCH_NO_INSTALL'):return False
    target=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'NexSearch/Employee'/trust['host_id']
    if program.resolve()==target.resolve():return False
    exe=target/'NexSearch.exe'
    if not exe.exists():
        target.mkdir(parents=True,exist_ok=True)
        shutil.copytree(program,target,dirs_exist_ok=True,ignore=shutil.ignore_patterns('Данные хоста','*.log','updates','data'))
    elif (program/'package-info.json').is_file():
        home=Path(os.environ['NEXOL_HOME']) if os.environ.get('NEXOL_HOME') else Path(os.environ.get('APPDATA',Path.home()/'.local/share'))/'Nexol'/trust['host_id']
        if upgrade_existing(program,target,home):return True
    subprocess.Popen([str(exe)],cwd=target,creationflags=subprocess.CREATE_NO_WINDOW)
    return True
