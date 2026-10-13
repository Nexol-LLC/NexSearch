from pathlib import Path
import json,zipfile,hashlib
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from network.updates import prepare,apply,rollback,UpdateError

def packet(tmp_path,content=b'new-code',name='EmployeeWindows/app.txt',alter=False):
 key=Ed25519PrivateKey.generate();program=tmp_path/'program';program.mkdir(exist_ok=True)
 (program/'updates-public.pem').write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
 raw=json.dumps({'version':'1.4.1','role':'EmployeeWindows','schema':14,'files':{name:hashlib.sha256(content).hexdigest()}}).encode()
 archive=tmp_path/'update.zip'
 with zipfile.ZipFile(archive,'w') as z:z.writestr('release.json',raw);z.writestr('release.sig',key.sign(raw));z.writestr(name,content+b'x' if alter else content)
 return program,archive

def test_signed_update_and_rollback_preserve_data(tmp_path):
 program,archive=packet(tmp_path);(program/'app.txt').write_bytes(b'old-code');(program/'Данные хоста').mkdir();(program/'Данные хоста/important.txt').write_text('customer-data')
 result=prepare(archive,program,tmp_path/'state','EmployeeWindows');assert result['verified']
 installed=apply(result['state_file']);assert (program/'app.txt').read_bytes()==b'new-code';assert (program/'Данные хоста/important.txt').read_text()=='customer-data'
 rollback(installed['rollback']);assert (program/'app.txt').read_bytes()==b'old-code'

def test_signature_role_hash_and_traversal_checked(tmp_path):
 program,archive=packet(tmp_path,alter=True)
 with pytest.raises(UpdateError):prepare(archive,program,tmp_path/'state','EmployeeWindows')
 with pytest.raises(UpdateError):prepare(archive,program,tmp_path/'state','HostWindows')
 program,archive=packet(tmp_path,name='EmployeeWindows/../outside.txt')
 with pytest.raises(UpdateError):prepare(archive,program,tmp_path/'state','EmployeeWindows')
 assert not (tmp_path/'outside.txt').exists()
 with zipfile.ZipFile(archive,'a') as z:z.writestr('unapproved.py','print(1)')
 with pytest.raises(UpdateError):prepare(archive,program,tmp_path/'state','EmployeeWindows')
