"""Build the old signed ZIP envelope around native executables. No private data."""
from pathlib import Path
import argparse,base64,hashlib,json,os,time,zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT=Path(__file__).resolve().parents[2]

def build(role,binaries,output,key,commit,trust_file=None,version='2.0.0'):
    assert role in ('HostUbuntu','EmployeeWindows','EmployeeMac')
    # The unmodified 1.6.7 client parses numeric versions. Never send alpha/beta.
    assert len(version.split('.'))==3 and all(p.isdigit() for p in version.split('.'))
    binaries=Path(binaries);files={};runtime='rust'
    if role=='HostUbuntu':
        import sys
        sys.path.insert(0,str(ROOT))
        from package_update import source_files
        files={name:p.read_bytes() for p,name in source_files() if not name.startswith('network/bin/')}
        files['nexsearch-host']=(binaries/'nexsearch-host').read_bytes()
        runtime='rust-transition'
    elif role=='EmployeeWindows':
        for name in ('NexSearch.exe','NexSearch Maintenance.exe'):files[name]=(binaries/name).read_bytes()
    else:
        for name in ('NexSearch','NexSearch Maintenance'):files[name]=(binaries/name).read_bytes()
        # The old installer invokes this once after applying the native package.
        files['network/mac_entry.py']=(ROOT/'network/mac_entry.py').read_bytes()
    trust=json.loads(Path(trust_file or ROOT/'network/client-trust.json').read_text(encoding='utf-8'))
    from uuid import UUID
    UUID(trust['host_id']);assert set(trust)=={'host_id','fingerprint'} and len(trust['fingerprint'])==64 and all(c in '0123456789abcdef' for c in trust['fingerprint'])
    files['client-trust.json']=json.dumps(trust,sort_keys=True).encode()
    if role in ('EmployeeMac','HostUbuntu'):files['network/client-trust.json']=files['client-trust.json']
    files['updates-public.pem']=(ROOT/'updates-public.pem').read_bytes()
    profile=ROOT/'deployment/internet-access.json'
    if profile.is_file():
        import sys
        sys.path.insert(0,str(ROOT))
        from network.internet_access import validate_profile
        raw=json.loads(profile.read_text(encoding='utf-8'))
        if raw.get('host_id')==trust['host_id']:files['internet-access.json']=json.dumps(validate_profile(raw,trust),sort_keys=True).encode()
    metadata={'version':version,'schema':19,'role':role,'runtime':runtime,'source_commit':commit,'built_at':int(time.time())}
    files['package-info.json']=json.dumps(metadata,indent=2).encode()
    manifest={**metadata,'files':{role+'/'+n:hashlib.sha256(v).hexdigest() for n,v in sorted(files.items())}}
    raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode();output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as packet:
        packet.writestr('release.json',raw);packet.writestr('release.sig',key.sign(raw))
        for name,value in sorted(files.items()):packet.writestr(role+'/'+name,value)
    assert output.stat().st_size<=256*1024*1024,'Package exceeds the installed client download limit'
    print(json.dumps({'role':role,'version':version,'runtime':runtime,'size_bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}))
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('role');parser.add_argument('--binaries',required=True,type=Path);parser.add_argument('--output',required=True,type=Path);parser.add_argument('--commit',required=True);parser.add_argument('--trust',type=Path);args=parser.parse_args()
    key=Ed25519PrivateKey.from_private_bytes(base64.b64decode(os.environ['NEXSEARCH_SIGNING_KEY']))
    build(args.role,args.binaries,args.output,key,args.commit,args.trust)
