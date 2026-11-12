"""Actual old Python installer -> native Rust installer contract, in temp folders."""
from pathlib import Path
import hashlib,json,subprocess,sys,zipfile
import os
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from network.updates import prepare,apply
from network.daily_updates import older_package
from network import secure_store

@pytest.fixture
def kit(tmp_path):
    program=tmp_path/'installed';program.mkdir();home=tmp_path/'home';home.mkdir()
    key=Ed25519PrivateKey.generate()
    (program/'updates-public.pem').write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    (program/'package-info.json').write_text(json.dumps({'version':'1.6.7','source_commit':'old'}))
    (program/'NexSearch.exe').write_bytes(b'old-program-fixture')
    secure_store.write(home/'employee.sec',{'token':'test-session-only','profile':{'id':'test-owner'}})
    session_bytes=(home/'employee.sec').read_bytes()
    files={'NexSearch.exe':b'native-program-fixture','NexSearch Maintenance.exe':b'native-installer-fixture','package-info.json':json.dumps({'version':'2.0.0','source_commit':'new','runtime':'rust'}).encode()}
    manifest={'version':'2.0.0','source_commit':'new','role':'EmployeeWindows','schema':19,'runtime':'rust','files':{'EmployeeWindows/'+n:hashlib.sha256(v).hexdigest() for n,v in files.items()}}
    raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode();archive=tmp_path/'signed.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('release.json',raw);z.writestr('release.sig',key.sign(raw))
        for name,value in files.items():z.writestr('EmployeeWindows/'+name,value)
    return program,home,archive,session_bytes

def helper():
    path=Path(os.environ.get('NEXSEARCH_TEST_NATIVE_HELPER',ROOT/'next/target/debug'/('nexsearch-maintenance.exe' if sys.platform=='win32' else 'nexsearch-maintenance')))
    assert path.is_file(),'Build the real native helper before migration acceptance'
    return path

def native(*args,ok=True):
    result=subprocess.run([str(helper()),*map(str,args)],capture_output=True,text=True,encoding='utf-8',timeout=30)
    if ok:assert result.returncode==0,result.stderr
    else:assert result.returncode!=0,'Invalid update unexpectedly accepted'
    return result

def test_unmodified_167_can_install_numeric_20_without_changing_protected_login(kit):
    program,home,archive,session_bytes=kit
    assert not older_package({'version':'1.6.7'},{'version':'2.0.0'})
    staged=prepare(archive,program,home,'EmployeeWindows')
    result=apply(staged['state_file'])
    assert result['installed']=='2.0.0'
    assert (program/'NexSearch.exe').read_bytes()==b'native-program-fixture'
    assert (home/'employee.sec').read_bytes()==session_bytes
    assert secure_store.read(home/'employee.sec',{})['token']=='test-session-only'

def test_native_helper_applies_the_stage_created_by_unmodified_167_and_rolls_back(kit):
    program,home,archive,session_bytes=kit
    staged=prepare(archive,program,home,'EmployeeWindows')
    result=json.loads(native('apply','--state',staged['state_file'],'--home',home).stdout)
    assert result['installed']=='2.0.0'
    assert json.loads((home/'updates/daily.json').read_text())['phase']=='installed'
    native('rollback','--folder',result['rollback'])
    assert (program/'NexSearch.exe').read_bytes()==b'old-program-fixture'
    assert (home/'employee.sec').read_bytes()==session_bytes
    assert not (program/'NexSearch Maintenance.exe').exists()

def test_python_signed_archive_is_verified_by_real_rust_executable(kit):
    program,home,archive,_=kit
    result=json.loads(native('prepare','--archive',archive,'--program',program,'--home',home,'--role','EmployeeWindows').stdout)
    stage=Path(result['state_file']).parent
    (stage/'NexSearch.exe').write_bytes(b'changed-after-validation')
    native('apply','--state',result['state_file'],ok=False)
    assert (program/'NexSearch.exe').read_bytes()==b'old-program-fixture'
