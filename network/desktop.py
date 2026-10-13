"""Open the isolated native shell; the business server remains in Python."""
from pathlib import Path
import os,subprocess,sys,webbrowser

def open_window(url,home,program,kind='employee'):
 if os.environ.get('NEXOL_NO_BROWSER'):return None
 root=Path(os.environ.get('NEXSEARCH_DESKTOP',Path(program)/'desktop'))
 executable=root/'runtime/electron.exe' if sys.platform=='win32' else root/'runtime/Electron.app/Contents/MacOS/Electron'
 if not executable.exists():
  webbrowser.open(url);return None
 command=[str(executable),str(root/'app'),'--url='+url,'--profile='+str(Path(home)/'desktop'),'--home='+str(home),'--program='+str(program),'--role='+kind]
 if os.environ.get('NEXSEARCH_HIDDEN'):command.append('--hidden')
 env=os.environ.copy()
 for name in ('ELECTRON_RUN_AS_NODE','NODE_OPTIONS','NEXOL_DATABASE_URL','CHECKO_API_KEY','NEXSEARCH_KEY_FILE','NEXSEARCH_SECRET_FILE'):env.pop(name,None)
 return subprocess.Popen(command,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
