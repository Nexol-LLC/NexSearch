"""Copy a portable employee distribution to a stable per-user location once."""
from pathlib import Path
import os,sys,json,shutil,subprocess

def launch_installed(program,trust):
    if sys.platform!='win32' or not getattr(sys,'frozen',False) or os.environ.get('NEXSEARCH_NO_INSTALL'):return False
    target=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'NexSearch/Employee'/trust['host_id']
    if program.resolve()==target.resolve():return False
    exe=target/'NexSearch.exe'
    if not exe.exists():
        target.mkdir(parents=True,exist_ok=True)
        shutil.copytree(program,target,dirs_exist_ok=True,ignore=shutil.ignore_patterns('Данные хоста','*.log','updates','data'))
    subprocess.Popen([str(exe)],cwd=target,creationflags=subprocess.CREATE_NO_WINDOW)
    return True
