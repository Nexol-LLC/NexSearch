"""Manual native-engine onboarding smoke; never logs auth links or enrolls an account."""
from pathlib import Path
import json,queue,subprocess,sys,threading,time
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from network.remote_access import helper_path,CONTROL

def main():
    results=[]
    for mode in ('host','employee'):
        directory=ROOT/'preview/vpn-onboarding-smoke'/mode
        directory.mkdir(parents=True,exist_ok=True)
        process=subprocess.Popen([str(helper_path())],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        events=queue.Queue()
        def collect():
            for line in process.stdout:
                try:events.put(json.loads(line))
                except ValueError:pass
        threading.Thread(target=collect,daemon=True).start()
        try:
            process.stdin.write(json.dumps({'mode':mode,'dir':str(directory),'hostname':'nexsearch-smoke-'+mode,'target':'nexsearch-smoke-host','control_url':CONTROL,'port':8743})+'\n');process.stdin.flush()
            deadline=time.monotonic()+35;phase=''
            while time.monotonic()<deadline:
                try:event=events.get(timeout=1)
                except queue.Empty:continue
                phase=event.get('phase')
                if phase=='needs_login' and event.get('auth_url'):
                    assert urlsplit(event['auth_url']).hostname in ('login.tailscale.com','controlplane.tailscale.com')
                    results.append({'mode':mode,'phase':phase,'login_link_valid':True});break
                if phase=='error':raise RuntimeError('Native VPN engine startup failed')
            else:raise RuntimeError('Native VPN onboarding timeout; phase='+str(phase))
        finally:
            if process.poll() is None:
                process.stdin.write('stop\n');process.stdin.flush()
                try:process.wait(timeout=12)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
        assert process.returncode==0,'Engine did not shut down cleanly'
    (ROOT/'preview/vpn-onboarding-smoke/result.json').write_text(json.dumps(results,indent=2))
    print('Native host and employee VPN engines: first-login link and clean shutdown passed. No account was enrolled.')

if __name__=='__main__':main()
