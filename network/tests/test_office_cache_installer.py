from pathlib import Path
import hashlib,importlib.util,io,json,zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import pytest
from network.updates import UpdateError

spec=importlib.util.spec_from_file_location('office_cache_installer',Path(__file__).resolve().parents[2]/'ubuntu/tunnel/install-from-cache.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

def packet(tmp_path,signing=None):
    key=signing or Ed25519PrivateKey.generate();public=tmp_path/'public';public.mkdir(exist_ok=True)
    (public/'updates-public.pem').write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    script=b'#!/bin/bash\necho verified-fixture\n'
    manifest={'role':'OfficeTunnel','version':'1.6.7','source_commit':'current','schema':19,
              'files':{'OfficeTunnel/connect-office.sh':hashlib.sha256(script).hexdigest()}}
    raw=json.dumps(manifest).encode();data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('release.json',raw);z.writestr('release.sig',key.sign(raw));z.writestr('OfficeTunnel/connect-office.sh',script)
    program=tmp_path/'program';program.mkdir(exist_ok=True);(program/'package-info.json').write_text(json.dumps({'version':'1.6.7','source_commit':'current'}))
    work=tmp_path/'work';work.mkdir(exist_ok=True)
    return program,public,work,data.getvalue()

def test_cached_signed_connector_runs_without_a_new_github_login(tmp_path,monkeypatch):
    program,public,work,data=packet(tmp_path);calls=[]
    monkeypatch.setattr(installer.subprocess,'check_output',lambda args:data)
    monkeypatch.setattr(installer.subprocess,'run',lambda args,**kw:calls.append(args))
    installer.install_from_cache(program,tmp_path/'repo.git',public,work)
    assert calls[0][0]=='bash' and Path(calls[0][1]).is_relative_to(work)
    assert Path(calls[0][1]).read_bytes().startswith(b'#!/bin/bash')

def test_old_cached_connector_not_executed_after_newer_host_update(tmp_path,monkeypatch):
    program,public,work,data=packet(tmp_path)
    (program/'package-info.json').write_text(json.dumps({'version':'1.6.8','source_commit':'newer'}))
    monkeypatch.setattr(installer.subprocess,'check_output',lambda args:data)
    monkeypatch.setattr(installer.subprocess,'run',lambda *a,**kw:pytest.fail('Stale connector executed'))
    with pytest.raises(RuntimeError):installer.install_from_cache(program,tmp_path/'repo.git',public,work)

def test_unsigned_cache_never_executes_as_root(tmp_path,monkeypatch):
    program,public,work,data=packet(tmp_path)
    wrong=Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    (public/'updates-public.pem').write_bytes(wrong)
    monkeypatch.setattr(installer.subprocess,'check_output',lambda args:data)
    monkeypatch.setattr(installer.subprocess,'run',lambda *a,**kw:pytest.fail('Unverified connector executed'))
    with pytest.raises(UpdateError):installer.install_from_cache(program,tmp_path/'repo.git',public,work)
