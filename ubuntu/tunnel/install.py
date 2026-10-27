"""Install an operator-generated role bundle, without touching NexSearch or its DB."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json,os,platform,shutil,socket,subprocess,sys,tarfile,tempfile,tomllib,urllib.request

def unit(role):
    binary='frps' if role=='gateway' else 'frpc'
    return f'''[Unit]
Description=NexSearch {role} reverse tunnel
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=nexsearch-tunnel
Group=nexsearch-tunnel
WorkingDirectory=/opt/nexsearch-tunnel
ExecStart=/opt/nexsearch-tunnel/{binary} -c /etc/nexsearch-tunnel/{binary}.toml
Restart=always
RestartSec=5
TimeoutStopSec=15
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
StateDirectory=nexsearch-tunnel
ReadWritePaths=/var/lib/nexsearch-tunnel
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet={'CAP_NET_BIND_SERVICE' if role=='gateway' else ''}
AmbientCapabilities={'CAP_NET_BIND_SERVICE' if role=='gateway' else ''}

[Install]
WantedBy=multi-user.target
'''

def download_binary(role,manifest,folder,offline=None):
    arch={'x86_64':'amd64','aarch64':'arm64'}.get(platform.machine())
    if not arch:raise ValueError('Only Linux amd64/arm64 is supported')
    version=manifest['version'];name=f'frp_{version}_linux_{arch}';archive=folder/'frp.tar.gz'
    if offline:shutil.copyfile(offline,archive)
    else:
        request=urllib.request.Request(f'https://github.com/fatedier/frp/releases/download/v{version}/{name}.tar.gz',headers={'User-Agent':'NexSearch deployment'})
        with urllib.request.urlopen(request,timeout=30) as response,archive.open('wb') as out:
            total=0
            while chunk:=response.read(1024*1024):
                total+=len(chunk)
                if total>64*1024**2:raise ValueError('Unexpected FRP archive size')
                out.write(chunk)
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=manifest['linux_'+arch]:raise ValueError('FRP checksum mismatch')
    executable='frps' if role=='gateway' else 'frpc';target=folder/executable
    with tarfile.open(archive) as tar:
        member=tar.getmember(name+'/'+executable)
        if not member.isfile():raise ValueError('Unexpected archive member')
        with tar.extractfile(member) as data:target.write_bytes(data.read())
        license_member=tar.getmember(name+'/LICENSE')
        if not license_member.isfile():raise ValueError('Missing FRP license')
        with tar.extractfile(license_member) as data:(folder/'LICENSE.frp').write_bytes(data.read())
    target.chmod(0o755);return target

def main():
    p=argparse.ArgumentParser();p.add_argument('--role',choices=['office','gateway'],required=True);p.add_argument('--bundle',type=Path,required=True);p.add_argument('--archive',type=Path);p.add_argument('--install',action='store_true');a=p.parse_args()
    bundle=a.bundle.resolve();binary='frps' if a.role=='gateway' else 'frpc'
    config=tomllib.loads((bundle/(binary+'.toml')).read_text(encoding='utf-8'))
    required=[binary+'.toml','ca.crt','tunnel.crt','tunnel.key','control.token']
    if any(not (bundle/name).is_file() or (bundle/name).is_symlink() for name in required):raise ValueError('Incomplete or symbolic-linked deployment bundle')
    # Secrets and private certificates must remain file references.
    if config.get('auth',{}).get('tokenSource',{}).get('file',{}).get('path')!='/etc/nexsearch-tunnel/control.token':raise ValueError('Unexpected token source')
    if a.role=='gateway':
        allowed=config.get('allowPorts');assert len(allowed)==1 and set(allowed[0])=={'single'}
        ports=(config['bindPort'],allowed[0]['single'])
    else:
        proxies=config.get('proxies',[])
        if len(proxies)!=1 or proxies[0].get('type')!='tcp' or proxies[0].get('localIP')!='127.0.0.1' or proxies[0].get('localPort')!=8743:raise ValueError('Office bundle must publish only the NexSearch HTTPS API')
        ports=()
    if not a.install:print(json.dumps({'valid':True,'role':a.role,'public_listener_ports':ports,'install_requested':False}));return
    if sys.platform!='linux' or os.geteuid()!=0:raise SystemExit('Install from a root shell on Ubuntu')
    import pwd
    service='nexsearch-tunnel-'+a.role
    if subprocess.run(['systemctl','is-active','--quiet',service]).returncode==0:raise SystemExit('Tunnel already running. Use a planned credential rotation; do not replace a live bundle.')
    for port in ports:
        with socket.socket() as probe:
            try:probe.bind(('0.0.0.0',port))
            except OSError:raise SystemExit(f'Port {port} is in use; no existing service was stopped')
    try:account=pwd.getpwnam('nexsearch-tunnel')
    except KeyError:
        subprocess.run(['useradd','--system','--home-dir','/var/lib/nexsearch-tunnel','--shell','/usr/sbin/nologin','nexsearch-tunnel'],check=True);account=pwd.getpwnam('nexsearch-tunnel')
    install_dir=Path('/opt/nexsearch-tunnel');config_dir=Path('/etc/nexsearch-tunnel');install_dir.mkdir(mode=0o755,exist_ok=True);config_dir.mkdir(mode=0o750,exist_ok=True)
    marker=config_dir/'role.json'
    if marker.exists() and json.loads(marker.read_text())['role']!=a.role:raise SystemExit('Office and gateway roles must be deployed on separate machines')
    os.chown(config_dir,0,account.pw_gid);config_dir.chmod(0o750)
    saved=install_dir/'rollback'/datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f');saved.mkdir(parents=True,mode=0o700)
    unit_path=Path('/etc/systemd/system')/(service+'.service');targets=[*(config_dir/name for name in required),install_dir/binary,install_dir/'LICENSE.frp',unit_path,marker]
    previous={}
    for i,target in enumerate(targets):
        previous[target]=saved/str(i) if target.exists() else None
        if previous[target]:shutil.copy2(target,previous[target])
    with tempfile.TemporaryDirectory(prefix='nexsearch-tunnel-') as temporary:
        executable=download_binary(a.role,json.loads(Path(__file__).with_name('frp-release.json').read_text()),Path(temporary),a.archive)
        # Verify syntax using final paths before activating the service.
        backup=Path(temporary)/'previous';backup.mkdir();changed=[]
        try:
            for name in required:
                target=config_dir/name
                if target.exists():shutil.copy2(target,backup/name)
                changed.append(name);shutil.copyfile(bundle/name,target);os.chown(target,account.pw_uid,account.pw_gid);target.chmod(0o600)
            subprocess.run([str(executable),'verify','-c',str(config_dir/(binary+'.toml'))],check=True,stdout=subprocess.DEVNULL)
        except Exception:
            for name in changed:
                if (backup/name).exists():shutil.copyfile(backup/name,config_dir/name)
                else:(config_dir/name).unlink(missing_ok=True)
            raise
        destination=install_dir/binary;shutil.copyfile(executable,destination);destination.chmod(0o755)
        shutil.copyfile(Path(temporary)/'LICENSE.frp',install_dir/'LICENSE.frp')
    try:
        unit_path.write_text(unit(a.role),encoding='utf-8');marker.write_text(json.dumps({'role':a.role}));marker.chmod(0o600)
        subprocess.run(['systemctl','daemon-reload'],check=True);subprocess.run(['systemctl','enable','--now',service],check=True)
        subprocess.run(['systemctl','is-active','--quiet',service],check=True)
    except Exception:
        subprocess.run(['systemctl','stop',service],check=False)
        for target,original in previous.items():
            if original:shutil.copy2(original,target)
            else:target.unlink(missing_ok=True)
        subprocess.run(['systemctl','daemon-reload'],check=False)
        raise
    print(json.dumps({'installed':True,'role':a.role,'service':service,'database_modified':False,'firewall_modified':False}))

if __name__=='__main__':main()
