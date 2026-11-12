"""Root migration orchestration is simulated; no system services are touched."""
from pathlib import Path
import importlib.util,json,sys,types
import pytest

@pytest.fixture
def updater(tmp_path,monkeypatch):
    if sys.platform=='win32':monkeypatch.setitem(sys.modules,'fcntl',types.SimpleNamespace())
    source=Path(__file__).resolve().parents[2]/'ubuntu/updater.py'
    spec=importlib.util.spec_from_file_location('native_transition_updater',source);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.BASE=tmp_path/'opt';module.CACHE=tmp_path/'cache';module.PACKAGES=tmp_path/'updates';module.SYSTEM_UNIT=tmp_path/'nexsearch.service'
    for path in (module.BASE/'current',module.CACHE,module.PACKAGES):path.mkdir(parents=True,exist_ok=True)
    metadata={'runtime':'rust-transition','version':'2.0.0','source_commit':'candidate'}
    current=module.BASE/'current';(current/'package-info.json').write_text(json.dumps(metadata));(current/'nexsearch-host').write_bytes(b'signed-native')
    module.SYSTEM_UNIT.write_text('[Service]\nExecStart=/opt/nexsearch/venv/bin/python -m network.server\n')
    def verified(*args):
        stage=tmp_path/'verified';stage.mkdir();(stage/'package-info.json').write_text(json.dumps(metadata));(stage/'nexsearch-host').write_bytes(b'signed-native');(stage/'update-state.json').write_text('{}');return {'state_file':str(stage/'update-state.json')}
    monkeypatch.setattr(module,'verified',verified);monkeypatch.setattr(module,'atomic',lambda p,v:p.write_text(json.dumps(v)))
    calls=[];monkeypatch.setattr(module,'run',lambda *a,**k:calls.append(a));monkeypatch.setattr(module,'audit',lambda *a:None);monkeypatch.setattr(module.subprocess,'check_output',lambda *a,**k:'0')
    return module,calls

def test_second_automatic_retry_switches_to_native_after_backup_and_verified_health(updater,monkeypatch):
    module,calls=updater;monkeypatch.setattr(module,'healthy',lambda:True);state={}
    assert module.retry_needed(state)
    module.activate_native(state)
    assert 'ExecStart=/opt/nexsearch/current/nexsearch-host' in module.SYSTEM_UNIT.read_text()
    assert state['host_runtime']=='rust' and not module.native_pending()
    backup=next(i for i,c in enumerate(calls) if any('backup_pg.py' in str(x) for x in c))
    stop=calls.index(('systemctl','stop','nexsearch'));assert backup<stop
    assert not (module.PACKAGES/'maintenance.json').exists()

def test_failed_native_health_restores_the_previous_unit(updater,monkeypatch):
    module,calls=updater;original=module.SYSTEM_UNIT.read_bytes();monkeypatch.setattr(module,'healthy',lambda:False);state={}
    with pytest.raises(RuntimeError):module.activate_native(state)
    assert module.SYSTEM_UNIT.read_bytes()==original
    assert state['host_runtime']=='legacy-rollback'
    assert ('systemctl','restart','nexsearch') in calls
    assert not (module.PACKAGES/'maintenance.json').exists()

def test_changed_native_file_is_rejected_before_services_are_stopped(updater,monkeypatch):
    module,calls=updater;(module.BASE/'current/nexsearch-host').write_bytes(b'tampered');state={}
    with pytest.raises(ValueError):module.activate_native(state)
    assert not calls

def test_live_search_keeps_the_claim_freeze_until_next_automatic_retry(updater,monkeypatch):
    module,calls=updater;monkeypatch.setattr(module.subprocess,'check_output',lambda *a,**k:'2');state={}
    module.activate_native(state)
    assert state['retry_after_seconds']==300 and module.native_pending()
    assert (module.PACKAGES/'maintenance.json').is_file()
    assert not calls
