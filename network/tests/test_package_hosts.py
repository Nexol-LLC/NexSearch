import json,zipfile
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import package_update

@pytest.mark.parametrize('role',['EmployeeWindows','EmployeeMac'])
def test_local_employee_package_pins_the_local_host(tmp_path,monkeypatch,role):
    monkeypatch.setattr(package_update,'ROOT',tmp_path)
    (tmp_path/'version.json').write_text(json.dumps({'version':'1.6.2','schema':17}))
    program=tmp_path/'dist/EmployeeWindows';program.mkdir(parents=True)
    (program/'client-trust.json').write_text('{"office":true}')
    maintenance=tmp_path/'dist/Maintenance';maintenance.mkdir()
    (maintenance/'NexSearch Maintenance.exe').write_bytes(b'test-only')
    network=tmp_path/'network';network.mkdir()
    (network/'client-trust.json').write_text('{"office":true}')
    monkeypatch.setattr(package_update,'source_files',lambda:iter([(network/'client-trust.json','network/client-trust.json')]))
    trust={'host_id':'dc21a456-2373-43f7-941c-deac7ecb87b9','fingerprint':'e6afa45904130cf279d1b3cd06af5126a0f6468d3de96ccb496e1d5918f0f013'}
    config=tmp_path/'local.json';config.write_text(json.dumps(trust));key=Ed25519PrivateKey.generate();output=tmp_path/'packet.zip'
    package_update.package(role,output,key,'test-commit',config)
    with zipfile.ZipFile(output) as packet:
        raw=packet.read('release.json');key.public_key().verify(packet.read('release.sig'),raw)
        assert json.loads(raw)['host_id']==trust['host_id']
        assert json.loads(packet.read(role+'/client-trust.json'))==trust
        if role=='EmployeeMac':assert json.loads(packet.read(role+'/network/client-trust.json'))==trust
    assert json.loads((network/'client-trust.json').read_text())=={'office':True}

def test_trust_override_rejects_private_material(tmp_path,monkeypatch):
    monkeypatch.setattr(package_update,'ROOT',tmp_path)
    (tmp_path/'version.json').write_text('{"version":"1.6.2","schema":17}')
    network=tmp_path/'network';network.mkdir();(network/'client-trust.json').write_text('{}')
    monkeypatch.setattr(package_update,'source_files',lambda:iter([]))
    config=tmp_path/'bad.json';config.write_text(json.dumps({'host_id':'dc21a456-2373-43f7-941c-deac7ecb87b9','fingerprint':'a'*64,'private_key':'must-not-be-shipped'}))
    with pytest.raises(ValueError):package_update.package('EmployeeMac',tmp_path/'packet.zip',Ed25519PrivateKey.generate(),'test',config)
