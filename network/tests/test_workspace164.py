from test_crm import db,pg,users,http,header,item
from test_v16 import staff,task_body
from network.notifications import overdue_escalations
from datetime import datetime,timedelta,timezone
from pathlib import Path
from openpyxl import load_workbook
import io


def test_carrier_inn_shared_validated_exported_and_preserved_for_older_clients(db):
    a,b=users(db);api=http(db);inn=item(42)['inn']
    saved=api.post('/api/carriers/save',headers=header(a),json={'name':'Новый перевозчик','inn':inn})
    assert saved.status_code==200,saved.text
    row=saved.json();assert row['inn']==inn
    assert api.get('/api/carriers',headers=header(b)).json()['carriers'][0]['inn']==inn
    # Older clients omit the field: their edits must not erase an existing INN.
    changed=api.post('/api/carriers/save',headers=header(b),json={'id':row['id'],'revision':row['revision'],'name':'Новое название'})
    assert changed.status_code==200,changed.text
    assert changed.json()['inn']==inn
    exported=api.get('/api/export/carriers',headers=header(a))
    book=load_workbook(io.BytesIO(exported.content));rows=list(book.worksheets[0].values)
    assert rows[0][:2]==('Наименование','ИНН') and rows[1][1]==inn
    for bad in ('111','7700000000','00000000000','<script>'):
        assert api.post('/api/carriers/save',headers=header(a),json={'name':'Ошибка','inn':bad}).status_code==422
    individual=api.post('/api/carriers',headers=header(a),json={'name':'ИП','inn':'500100732259'})
    assert individual.status_code==200,individual.text
    assert individual.json()['inn']=='500100732259'
    cleared=api.post('/api/carriers/save',headers=header(a),json={'id':row['id'],'revision':changed.json()['revision'],'name':'Без ИНН','inn':''})
    assert cleared.status_code==200 and cleared.json()['inn']==''


def test_overdue_escalations_work_without_executor_polling_and_respect_roles(db):
    a,b,manager,director,outside=staff(db);api=http(db)
    with db.transaction(admin=True) as c:c.execute("UPDATE employees SET role='manager' WHERE id=%s",(outside['employee']['id'],))
    past=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat()
    task=api.post('/api/tasks',headers=header(a),json=task_body(due_at=past)).json()
    overdue_escalations(db) # Host tick; the executor never opens notifications.
    for p in (manager,director):
        alerts=api.get('/api/notifications',headers=header(p)).json()['notifications']
        assert len(alerts)==1 and alerts[0]['kind']=='task.overdue' and alerts[0]['entity_id']==task['id']
        assert 'Анна' in alerts[0]['description'] and 'МСК' in alerts[0]['description']
        assert api.get('/api/tasks/'+task['id'],headers=header(p)).status_code==200
    for p in (b,outside):assert not api.get('/api/notifications',headers=header(p)).json()['notifications']
    assert api.get('/api/tasks/'+task['id'],headers=header(b)).status_code==404
    # Completed and future tasks do not escalate; a new overdue deadline alerts once.
    assert api.put('/api/tasks/'+task['id'],headers=header(a),json=task_body(due_at=past,status='done',revision=0)).status_code==200
    api.post('/api/notifications/clear',headers=header(manager),json={})
    overdue_escalations(db)
    assert not api.get('/api/notifications',headers=header(manager)).json()['notifications']
    api.post('/api/tasks',headers=header(a),json=task_body())
    overdue_escalations(db)
    assert not api.get('/api/notifications',headers=header(manager)).json()['notifications']
    future=api.put('/api/tasks/'+task['id'],headers=header(a),json=task_body(due_at=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat(),revision=1)).json()
    assert future['revision']==2
    for _ in range(3):overdue_escalations(db)
    assert api.get('/api/notifications',headers=header(manager)).json()['unread']==1


def test_notification_deletion_is_private_and_does_not_resurrect_reminders(db):
    a,b,manager,director,outside=staff(db);api=http(db)
    api.post('/api/tasks',headers=header(a),json=task_body(due_at=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()))
    alert=api.get('/api/notifications',headers=header(manager)).json()['notifications'][0]
    assert api.delete('/api/notifications/'+alert['id'],headers=header(b)).status_code==404
    assert api.delete('/api/notifications/'+alert['id'],headers=header(manager)).status_code==200
    for _ in range(2):assert api.get('/api/notifications',headers=header(manager)).json()['unread']==0
    assert api.get('/api/notifications',headers=header(director)).json()['unread']==1
    assert api.post('/api/notifications/clear',json={}).status_code==401
    assert api.post('/api/notifications/clear',headers=header(director),json={}).status_code==200
    assert api.post('/api/notifications/clear',headers=header(a),json={}).status_code==200
    # No synthetic new reminders after an app restart/poll, including executor ones.
    for p in (a,manager,director):
        result=http(db).get('/api/notifications',headers=header(p)).json()
        assert result['notifications']==[] and result['unread']==0


def test_schema19_additive_for_current_and_old_privileged_updaters(db):
    a,b=users(db);api=http(db)
    carrier=api.post('/api/carriers/save',headers=header(a),json={'name':'Сохранить'}).json()
    root=Path(__file__).resolve().parents[1]
    for migration in (None,'schema15.sql','schema16.sql','schema17.sql','schema18.sql'):
        with db.transaction(admin=True) as c:
            c.execute('ALTER TABLE carriers DROP COLUMN inn')
            c.execute('ALTER TABLE notifications DROP COLUMN deleted_at')
            c.execute('DELETE FROM schema_meta WHERE version=19')
            if migration:c.execute((root/migration).read_text(encoding='utf-8'))
        db.initialize()
        assert api.get('/api/carriers',headers=header(a)).json()['carriers'][0]['id']==carrier['id']
        with db.transaction(admin=True) as c:assert c.execute('SELECT 1 FROM schema_meta WHERE version=19').fetchone()
