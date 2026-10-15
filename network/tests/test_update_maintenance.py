from test_crm import db,pg,users,http,header
from pathlib import Path
import json,time,sys,types,importlib.util
import pytest
from network.update_maintenance import paused,can_start_search,running_searches,WORKER_LOCK

def test_queued_searches_do_not_block_install_and_are_not_deleted(db):
 a,b=users(db);api=http(db)
 jobs=[api.post('/api/search',headers=header(person),json={'target':10}).json()['id'] for person in (a,b)]
 with db.transaction(admin=True) as c:
  assert running_searches(c)==0
  rows=c.execute('SELECT id,status FROM jobs ORDER BY created_at').fetchall()
  assert [str(row['id']) for row in rows]==jobs
  assert all(row['status']=='queued' for row in rows)

def test_live_search_drains_but_abandoned_search_is_resumed_with_progress(db):
 a,b=users(db);api=http(db)
 job=api.post('/api/search',headers=header(a),json={'target':10}).json()['id']
 with db.transaction(admin=True) as c:c.execute("UPDATE jobs SET status='running',added=3 WHERE id=%s",(job,))
 with db.pool.connection() as lease:
  lease.execute('SELECT pg_advisory_lock(%s)',(WORKER_LOCK,));lease.commit()
  try:
   with db.transaction(admin=True) as c:
    assert running_searches(c)==1
    assert c.execute('SELECT status FROM jobs WHERE id=%s',(job,)).fetchone()['status']=='running'
  finally:lease.execute('SELECT pg_advisory_unlock(%s)',(WORKER_LOCK,));lease.commit()
 with db.transaction(admin=True) as c:
  assert running_searches(c)==0
  row=c.execute('SELECT status,added FROM jobs WHERE id=%s',(job,)).fetchone()
  assert row=={'status':'queued','added':3}
  assert c.execute("SELECT count(*) AS n FROM audit_events WHERE kind='update.search_recovered'").fetchone()['n']==1

def test_maintenance_pauses_claims_and_expires_after_a_crash(db,tmp_path,monkeypatch):
 state=tmp_path/'status.json';marker=tmp_path/'maintenance.json'
 monkeypatch.setenv('NEXSEARCH_UPDATE_STATE',str(state))
 assert not paused()
 marker.write_text(json.dumps({'expires_at':time.time()+600}))
 assert paused()
 with db.transaction(admin=True) as c:assert not can_start_search(c)
 marker.write_text(json.dumps({'expires_at':time.time()-1}))
 with db.transaction(admin=True) as c:assert can_start_search(c)
 marker.write_text('broken-json');assert not paused()

def updater_module(monkeypatch):
 try:import fcntl
 except ImportError:monkeypatch.setitem(sys.modules,'fcntl',types.SimpleNamespace())
 source=Path(__file__).resolve().parents[2]/'ubuntu/updater.py'
 spec=importlib.util.spec_from_file_location('test_root_updater',source);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def test_retry_only_fetches_when_pending_failed_or_interrupted(tmp_path,monkeypatch):
 module=updater_module(monkeypatch);monkeypatch.setattr(module,'PACKAGES',tmp_path)
 assert not module.retry_needed({'message':'Проверка завершена'})
 assert module.retry_needed({'host_deferred':'Поиск выполняется'})
 assert module.retry_needed({'error':'ConnectionError'})
 (tmp_path/'maintenance.json').write_text('{}');assert module.retry_needed({})

def test_timer_bootstrap_is_idempotent_and_keeps_daily_moscow_schedule(tmp_path,monkeypatch):
 module=updater_module(monkeypatch);base=tmp_path/'opt';source=base/'current/ubuntu';source.mkdir(parents=True)
 original=Path(__file__).resolve().parents[2]/'ubuntu'
 names=('nexsearch-update-retry.service','nexsearch-update-retry.timer')
 for name in names:(source/name).write_bytes((original/name).read_bytes())
 units=tmp_path/'systemd';units.mkdir();cache=tmp_path/'cache';cache.mkdir()
 actual_path=module.Path
 monkeypatch.setattr(module,'Path',lambda value:units if value=='/etc/systemd/system' else actual_path(value))
 monkeypatch.setattr(module,'BASE',base);monkeypatch.setattr(module,'CACHE',cache)
 commands=[];monkeypatch.setattr(module,'run',lambda *args:commands.append(args))
 module.configure_retry_units();module.configure_retry_units()
 assert commands==[('systemctl','daemon-reload'),('systemctl','enable','--now','nexsearch-update.timer','nexsearch-update-retry.timer')]
 assert 'OnUnitInactiveSec=5min' in (units/names[1]).read_text()
 assert '--retry' in (units/names[0]).read_text()
 assert '10:00:00 Europe/Moscow' in (original/'nexsearch-update.timer').read_text()

@pytest.mark.parametrize('running',[True,False])
def test_installer_defers_live_work_and_releases_pause_if_backup_fails(tmp_path,monkeypatch,running):
 module=updater_module(monkeypatch);base=tmp_path/'opt';current=base/'current';current.mkdir(parents=True)
 (current/'package-info.json').write_text(json.dumps({'source_commit':'old'}))
 packages=tmp_path/'packages';packages.mkdir();stage=tmp_path/'verified';stage.mkdir()
 (stage/'package-info.json').write_text(json.dumps({'source_commit':'new','version':'1.5.1'}))
 monkeypatch.setattr(module,'BASE',base);monkeypatch.setattr(module,'PACKAGES',packages)
 monkeypatch.setattr(module,'verified',lambda *_:{'state_file':str(stage/'update-state.json')})
 monkeypatch.setattr(module,'atomic',lambda path,value:path.write_text(json.dumps(value)))
 monkeypatch.setattr(module.subprocess,'check_output',lambda *args,**kwargs:'1' if running else '0')
 calls=[]
 def fail_backup(*args,**kwargs):calls.append(args);raise RuntimeError('Backup unavailable')
 monkeypatch.setattr(module,'run',fail_backup)
 state={}
 if running:
  module.host_install(tmp_path/'signed.zip','a'*64,state)
  assert state['host_deferred'] and state['retry_after_seconds']==300 and not calls
  assert (packages/'maintenance.json').exists() and not stage.exists()
 else:
  with pytest.raises(RuntimeError,match='Backup unavailable'):module.host_install(tmp_path/'signed.zip','a'*64,state)
  assert len(calls)==1 and calls[0][-1].endswith('backup_pg.py')
  assert not (packages/'maintenance.json').exists()
 assert json.loads((current/'package-info.json').read_text())['source_commit']=='old'
