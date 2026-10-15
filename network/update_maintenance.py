"""Drain running searches for a signed host update; keep queued work intact."""
from pathlib import Path
import json,os,time

UPDATE_LOCK=73142004
WORKER_LOCK=73142003

def maintenance_file():
 return Path(os.environ.get('NEXSEARCH_UPDATE_STATE','/var/lib/nexsearch-updates/status.json')).with_name('maintenance.json')

def paused():
 try:return float(json.loads(maintenance_file().read_text(encoding='utf-8'))['expires_at'])>time.time()
 except (OSError,ValueError,KeyError,TypeError):return False

def can_start_search(connection):
 if paused():return False
 connection.execute('SELECT pg_advisory_xact_lock(%s)',(UPDATE_LOCK,))
 return not paused()

def running_searches(connection):
 connection.execute('SELECT pg_advisory_xact_lock(%s)',(UPDATE_LOCK,))
 connection.execute("UPDATE jobs SET message='Ожидает обновления хоста; поиск продолжится автоматически' WHERE status='queued' AND NOT stop_requested")
 live=connection.execute('SELECT pg_try_advisory_xact_lock(%s) AS available',(WORKER_LOCK,)).fetchone()
 if live['available']:
  # No worker owns the session lease, so these rows cannot represent live tasks.
  recovered=connection.execute("UPDATE jobs SET status=CASE WHEN stop_requested THEN 'stopped' ELSE 'queued' END,finished_at=CASE WHEN stop_requested THEN now() ELSE NULL END,message='Поиск продолжится после обновления хоста' WHERE status='running' RETURNING id").fetchall()
  if recovered:connection.execute("INSERT INTO audit_events(kind,entity_id,details) VALUES('update.search_recovered','',jsonb_build_object('jobs',%s::integer))",(len(recovered),))
 return connection.execute("SELECT count(*) AS count FROM jobs WHERE status='running'").fetchone()['count']
