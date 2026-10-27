from pathlib import Path
import hashlib,json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import pytest
from network.install_client import upgrade_existing
from network.daily_updates import read_state

def kit(tmp_path):
    program=tmp_path/'download/EmployeeWindows';target=tmp_path/'installed';home=tmp_path/'home'
    proof=program.parent/'UpdateProof'
    for folder in (program,target,proof):folder.mkdir(parents=True,exist_ok=True)
    key=Ed25519PrivateKey.generate();pub=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    (target/'updates-public.pem').write_bytes(pub)
    (target/'package-info.json').write_text(json.dumps({'version':'1.6.6','source_commit':'old'}))
    (target/'NexSearch.exe').write_bytes(b'old app')
    files={'NexSearch.exe':b'new app','NexSearch Maintenance.exe':b'new helper','updates-public.pem':pub,
           'package-info.json':json.dumps({'version':'1.6.7','source_commit':'new','built_at':200}).encode()}
    for name,data in files.items():(program/name).write_bytes(data)
    manifest={'version':'1.6.7','source_commit':'new','role':'EmployeeWindows','schema':19,'built_at':200,
              'files':{'EmployeeWindows/'+name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
    raw=json.dumps(manifest).encode();(proof/'release.json').write_bytes(raw);(proof/'release.sig').write_bytes(key.sign(raw))
    return program,target,home

def test_full_kit_stages_signed_upgrade_instead_of_opening_old_installed_program(tmp_path,monkeypatch):
    program,target,home=kit(tmp_path);calls=[]
    monkeypatch.setattr('network.install_client.subprocess.Popen',lambda *a,**kw:calls.append((a,kw)))
    assert upgrade_existing(program,target,home)
    assert (target/'NexSearch.exe').read_bytes()==b'old app'  # helper applies after bootstrap exits
    state=read_state(home);assert state['pending_commit']=='new'
    args=calls[0][0][0];assert '--restart' in args and '--wait-pid' in args
    assert Path(args[0]).parent==home/'updates'

def test_tampered_full_kit_is_not_executed_or_installed(tmp_path,monkeypatch):
    program,target,home=kit(tmp_path);(program/'NexSearch Maintenance.exe').write_bytes(b'tampered')
    monkeypatch.setattr('network.install_client.subprocess.Popen',lambda *a,**kw:pytest.fail('Unverified helper executed'))
    with pytest.raises(ValueError):upgrade_existing(program,target,home)
    assert (target/'NexSearch.exe').read_bytes()==b'old app'

def test_full_kit_cannot_downgrade_newer_installed_copy(tmp_path):
    program,target,home=kit(tmp_path)
    (target/'package-info.json').write_text(json.dumps({'version':'1.6.8','source_commit':'future'}))
    assert not upgrade_existing(program,target,home)
