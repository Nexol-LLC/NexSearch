"""Native macOS launcher after offline runtimes have been unpacked."""
from pathlib import Path
import json,os,sys,traceback

def main():
    role=sys.argv[1]
    if role not in ('host','employee'):raise ValueError('Unknown program role')
    base=Path(__file__).resolve().parents[1]
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
