"""Private, persistent notifications; one reminder per task and due time."""
from uuid import uuid4
from zoneinfo import ZoneInfo

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
