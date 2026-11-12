"""Release layout contract: real signatures, old reader and executable Unix modes."""
from pathlib import Path
import importlib.util,json,sys,zipfile
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from network.updates import prepare

@pytest.mark.parametrize('role,names',[
    ('EmployeeWindows',('NexSearch.exe','NexSearch Maintenance.exe')),
    ('EmployeeMac',('NexSearch','NexSearch Maintenance')),
])
def test_native_layout_is_readable_by_167_and_preserves_public_host_identity(tmp_path,role,names):
    spec=importlib.util.spec_from_file_location('native_package_fixture',ROOT/'next/tools/package-native.py')
    package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package);package.ROOT=tmp_path
    network=tmp_path/'network';network.mkdir()
    trust={'host_id':'00000000-0000-4000-8000-000000000001','fingerprint':'a'*64}
    (network/'client-trust.json').write_text(json.dumps(trust));(network/'mac_entry.py').write_text('# stable legacy launcher shim')
    key=Ed25519PrivateKey.generate();public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    (tmp_path/'updates-public.pem').write_bytes(public)
    binaries=tmp_path/'bin';binaries.mkdir()
    for name in names:(binaries/name).write_bytes(b'native-binary-layout-fixture')
    archive=tmp_path/'release.zip';package.build(role,binaries,archive,key,'source-fixture')
    program=tmp_path/'installed';program.mkdir();(program/'updates-public.pem').write_bytes(public)
    staged=prepare(archive,program,tmp_path/'home',role)
    assert staged['verified'] and staged['version']=='2.0.0'
    stage=Path(staged['state_file']).parent
    assert json.loads((stage/'client-trust.json').read_text())==trust
    assert not (stage/'employee.sec').exists()
    with zipfile.ZipFile(archive) as z:
        manifest=json.loads(z.read('release.json'));key.public_key().verify(z.read('release.sig'),z.read('release.json'))
        assert manifest['runtime']=='rust'
        if role=='EmployeeMac':
            assert all((z.getinfo(role+'/'+name).external_attr>>16)&0o111 for name in names)
            assert (stage/'network/mac_entry.py').is_file()
