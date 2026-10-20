"""Private, persistent notifications; one reminder per task and due time."""
from uuid import uuid4
from zoneinfo import ZoneInfo
import logging
import threading

def notify(conn,owner,kind,title,description='',entity='',key=''):
    return conn.execute('''INSERT INTO notifications(id,employee_id,kind,title,description,entity_id,dedupe_key)
      VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(employee_id,dedupe_key) DO NOTHING RETURNING id''',
      (uuid4(),owner,kind,title[:300],description[:2000],str(entity),key or str(uuid4()))).fetchone()

def task_reminders(db,owner):
    with db.transaction(owner) as c:
        tasks=c.execute("SELECT * FROM followup_tasks WHERE employee_id=%s AND status='open' AND due_at<=now()+interval '15 minutes' ORDER BY due_at LIMIT 1000",(owner,)).fetchall()
        for task in tasks:
            due=task['due_at'].astimezone(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')+' МСК'
            notify(c,owner,'task.due',task['title'],'Срок задачи: '+due,task['id'],f"task:{task['id']}:due:{task['due_at'].isoformat()}")

def search_notifications(db,owner):
    with db.transaction(owner) as c:
        jobs=c.execute("SELECT id,status,added,message FROM jobs WHERE employee_id=%s AND status IN ('completed','partial','error','stopped') AND finished_at>now()-interval '24 hours' ORDER BY finished_at DESC LIMIT 30",(owner,)).fetchall()
        for job in jobs:
            title='Поиск завершён' if job['status'] in ('completed','partial') else 'Поиск остановлен' if job['status']=='stopped' else 'Ошибка поиска'
            notify(c,owner,'search.finished',title,f"Найдено компаний: {job['added']}. "+(job['message'] or ''),job['id'],f"search:{job['id']}:finished")


def overdue_escalations(db,recipient=None):
    """Independent of the executor being online. Never cross department boundaries.

    A retained dedupe key also prevents cleared alerts from coming back. A changed
    deadline creates a new escalation; completed tasks cannot create new alerts.
    """
    with db.transaction(admin=True) as c:
        rows=c.execute('''INSERT INTO notifications(id,employee_id,kind,title,description,entity_id,dedupe_key)
          SELECT gen_random_uuid(),boss.id,'task.overdue',left('Просрочена задача: '||t.title,300),
            left(e.first_name||' '||e.last_name||E'\nСрок: '||to_char(t.due_at AT TIME ZONE 'Europe/Moscow','DD.MM.YYYY HH24:MI')||' МСК'||E'\n'||t.description,2000),
            t.id::text,'overdue:'||t.id::text||':'||extract(epoch FROM t.due_at)::text
          FROM followup_tasks t JOIN employees e ON e.id=t.employee_id
          JOIN employees boss ON boss.active AND boss.approved AND
            (boss.role='director' OR (boss.role='manager' AND boss.department=e.department))
          WHERE t.status='open' AND t.due_at<now() AND e.role='logist' AND e.active AND e.approved
            AND (%s::uuid IS NULL OR boss.id=%s::uuid)
          ON CONFLICT(employee_id,dedupe_key) DO NOTHING RETURNING employee_id''',(recipient,recipient)).fetchall()
    for owner in {r['employee_id'] for r in rows}:db.events.publish(owner,'notification.created')


class ReminderWorker:
    def __init__(self,db):
        self.db=db;self.closed=threading.Event();self.thread=None
    def start(self):
        self.thread=threading.Thread(target=self.run,name='task-escalations',daemon=True);self.thread.start()
    def run(self):
        while not self.closed.is_set():
            try:overdue_escalations(self.db)
            except Exception:logging.getLogger(__name__).exception('Cannot generate overdue task reminders')
            self.closed.wait(30)
    def close(self):
        self.closed.set()
        if self.thread:self.thread.join(timeout=3)
