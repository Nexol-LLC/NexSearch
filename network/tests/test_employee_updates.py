"""Employee update state must describe installed files, not a downloaded archive."""
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import hashlib, io, json, threading, zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import pytest
from network import daily_updates as updater
from network.updates import apply


@pytest.fixture
def setup(tmp_path, monkeypatch):
    program=tmp_path/'program';program.mkdir();home=tmp_path/'home'
    key=Ed25519PrivateKey.generate()
    (program/'updates-public.pem').write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    installed={'version':'1.5.0','source_commit':'old'}
    (program/'package-info.json').write_text(json.dumps(installed))
    content=json.dumps({'version':'1.5.1','source_commit':'new'}).encode()
    files={'EmployeeWindows/package-info.json':hashlib.sha256(content).hexdigest()}
    manifest=json.dumps({'version':'1.5.1','source_commit':'new','role':'EmployeeWindows','schema':15,'files':files}).encode()
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        z.writestr('release.json',manifest);z.writestr('release.sig',key.sign(manifest));z.writestr('EmployeeWindows/package-info.json',content)
    archive=stream.getvalue()
    now=[datetime(2026,10,15,10,0,tzinfo=updater.MOSCOW)]
    monkeypatch.setattr(updater,'clock',lambda:now[0]);monkeypatch.setattr(updater,'role',lambda:'EmployeeWindows')
    info={'available':True,'version':'1.5.1','source_commit':'new','sha256':hashlib.sha256(archive).hexdigest(),'size_bytes':len(archive),'checked_at':now[0].isoformat()}
    calls=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):return dict(info)
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def iter_content(self,size):yield archive
    class Session:
        def get(self,url,**kwargs):calls.append(url);return Response()
    agent=SimpleNamespace(lock=threading.Lock(),url='https://host',state={'token':'test'},session=Session())
    return SimpleNamespace(program=program,home=home,now=now,info=info,agent=agent,calls=calls,archive=archive)


def check(s,force=True):return updater.check_update(s.program,s.home,{},s.agent,force)


def test_download_is_ready_until_files_are_applied(setup):
    s=setup;state=check(s)
    assert state['phase']=='ready'
    assert updater.local_status(s.program,s.home)['installed_version']=='1.5.0'
    # Rechecking must not call a merely downloaded package installed/current.
    again=check(s);assert again['phase']=='ready';assert len(s.calls)==3
    apply(state['pending_state']);updater.record_applied(s.home,state['pending_state'])
    assert check(s)['phase']=='current'
    assert updater.local_status(s.program,s.home)['installed_version']=='1.5.1'


def test_missing_stage_is_reprepared_from_verified_download(setup):
    s=setup;state=check(s);Path(state['pending_state']).unlink()
    state=check(s);assert state['phase']=='ready';assert Path(state['pending_state']).is_file()
    assert len([u for u in s.calls if u.endswith('/package')])==1


def test_stale_host_cache_does_not_suppress_whole_day(setup):
    s=setup;s.info.update(version='1.5.0',source_commit='old',checked_at=(s.now[0]-timedelta(days=1)).isoformat())
    assert check(s,False)['phase']=='current'
    assert 'date' not in updater.read_state(s.home)
    check(s,False);assert len(s.calls)==1
    s.now[0]+=timedelta(minutes=5)
    s.info.update(version='1.5.1',source_commit='new',checked_at=s.now[0].isoformat())
    assert check(s,False)['phase']=='ready'


def test_corrupt_download_is_not_staged_and_is_retried(setup):
    s=setup;s.info['sha256']='a'*64
    state=check(s);assert state['phase']=='error';assert 'pending_state' not in state;assert 'date' not in state
    assert state['last_error']=='ValueError'


def test_background_helper_and_ui_do_not_download_concurrently(setup):
    s=setup
    with updater.update_lock(s.home) as locked:
        assert locked
        assert updater.local_status(s.program,s.home)['checking']
        check(s);assert not s.calls
    assert not updater.local_status(s.program,s.home)['checking']
    assert check(s)['phase']=='ready'


def test_record_applied_uses_actual_package_preserves_newer_pending(setup):
    s=setup;state=check(s);first=state['pending_state']
    state.update(pending_state='newer-stage',pending_commit='newer');updater.write_state(s.home,state)
    apply(first);updater.record_applied(s.home,first)
    result=updater.read_state(s.home)
    assert result['installed_commit']=='new' and result['pending_state']=='newer-stage'


def test_failed_install_is_visible_and_keeps_pending(setup):
    s=setup;state=check(s);updater.record_apply_error(s.home,PermissionError())
    result=updater.read_state(s.home)
    assert result['phase']=='error' and result['last_error']=='PermissionError'
    assert result['pending_state']==state['pending_state']
