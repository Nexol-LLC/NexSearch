"""Sign verified native artifacts, then fast-forward both production branches atomically.

Run only in the release job, after all acceptance jobs have succeeded. No credentials
are stored in the repository or archives. A racing production push aborts promotion.
"""
from pathlib import Path
import argparse,base64,hashlib,importlib.util,json,os,shutil,subprocess,sys,tempfile,zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

def git(*args,**kwargs):
    return subprocess.check_output(['git',*args],cwd=ROOT,text=True,encoding='utf-8',**kwargs).strip()

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def verify(archive,key,commit,version):
    with zipfile.ZipFile(archive) as z:
        raw=z.read('release.json');key.verify(z.read('release.sig'),raw);m=json.loads(raw)
        assert m['version']==version and m['source_commit']==commit
        assert set(z.namelist())==set(m['files'])|{'release.json','release.sig'}
        for name,digest in m['files'].items():assert hashlib.sha256(z.read(name)).hexdigest()==digest,name
    # Exercise the installed client's exact ZIP reader, not only a custom verifier.
    from network.updates import prepare
    with tempfile.TemporaryDirectory() as folder:
        folder=Path(folder);program=folder/'program';program.mkdir()
        shutil.copy2(ROOT/'updates-public.pem',program/'updates-public.pem')
        prepared=prepare(archive,program,folder/'home',m['role'])
        assert prepared['version']==version
    return m

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',required=True,type=Path);p.add_argument('--output',default='.updates',type=Path);p.add_argument('--promote',action='store_true');a=p.parse_args()
    commit=git('rev-parse','HEAD');version=json.loads((ROOT/'version.json').read_text())['version']
    parts=version.split('.')
    assert len(parts)==3 and all(p.isdigit() for p in parts) and tuple(map(int,parts))>=(2,0,0)
    assert json.loads((ROOT/'.github/release-channel.json').read_text())['mode']=='native-release'
    key=Ed25519PrivateKey.from_private_bytes(base64.b64decode(os.environ['NEXSEARCH_SIGNING_KEY']))
    public=serialization.load_pem_public_key((ROOT/'updates-public.pem').read_bytes())
    assert key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)==public.public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw),'Signing key does not match installed trust'
    package=load(ROOT/'next/tools/package-native.py','native_package')
    a.output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        windows=Path(folder)
        for src,dest in [('nexsearch.exe','NexSearch.exe'),('nexsearch-maintenance.exe','NexSearch Maintenance.exe')]:shutil.copy2(a.input/'native-EmployeeWindows'/src,windows/dest)
        for role,binaries,name in [('HostUbuntu',a.input/'native-HostUbuntu','host-linux'),('EmployeeWindows',windows,'employee-windows'),('EmployeeMac',a.input/'native-EmployeeMac','employee-mac')]:
            package.build(role,binaries,a.output/(name+'.zip'),key,commit,version=version)
            if role.startswith('Employee'):package.build(role,binaries,a.output/(name+'-local.zip'),key,commit,ROOT/'deployment/local-client-trust.json',version=version)
    load(ROOT/'ubuntu/tunnel/release.py','tunnel_release').build(a.output/'host-office-connect.zip',commit)
    manifests={path.name:verify(path,public,commit,version) for path in sorted(a.output.glob('*.zip'))}
    assert len(manifests)==6
    (a.output/'native-release.json').write_text(json.dumps({'version':version,'source_commit':commit,'packages':{name:{k:m[k] for k in ('role','runtime') if k in m} for name,m in manifests.items()}},indent=2))
    print('All six packages verified with the installed-client reader; ready for atomic promotion.')
    if not a.promote:return
    assert os.environ.get('GITHUB_ACTIONS')=='true','Promotion is only available in the protected CI release context'
    git('fetch','origin','+refs/heads/host:refs/remotes/origin/host','+refs/heads/employe:refs/remotes/origin/employe')
    tips={branch:git('rev-parse','refs/remotes/origin/'+branch) for branch in ('host','employe')}
    for branch,tip in tips.items():assert tip==os.environ['EXPECTED_'+branch.upper()+'_TIP'],'Production branch changed during the build: '+branch
    git('config','user.name','NexSearch Builds');git('config','user.email','nexsearch-builds@users.noreply.github.com')
    git('add','--','.updates')
    tree=git('write-tree');commits={}
    for branch,tip in tips.items():
        args=['commit-tree',tree,'-p',tip]
        if tip!=commit:args+=['-p',commit]
        commits[branch]=git(*args,input=f'Publish native {version} and compatible signed updates ({commit})\n')
    git('push','--atomic','origin',*[sha+':refs/heads/'+branch for branch,sha in commits.items()])
    print(json.dumps({'promoted':commits,'source_commit':commit}))
    output=os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output,'a') as f:f.write('release_commit='+commits['host']+'\n')

if __name__=='__main__':main()
