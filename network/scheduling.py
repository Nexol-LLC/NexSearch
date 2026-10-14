"""Schedules are claimed atomically by the host, even with no connected employee."""
from uuid import uuid4
def enqueue_due(db):
 added=[]
 with db.transaction(admin=True) as c:
  rows=c.execute("SELECT s.* FROM search_schedules s JOIN employees e ON e.id=s.employee_id WHERE s.status='pending' AND s.run_at<=now() AND e.active AND (e.approved OR e.is_admin) ORDER BY s.run_at FOR UPDATE OF s SKIP LOCKED LIMIT 50").fetchall()
  for row in rows:
   # A busy employee's schedule remains pending until their previous search finishes.
   if c.execute("SELECT 1 FROM jobs WHERE employee_id=%s AND status IN ('queued','running')",(row['employee_id'],)).fetchone():continue
   ident=uuid4()
   result=c.execute("INSERT INTO jobs(id,employee_id,target,criteria,message) VALUES(%s,%s,%s,%s,'Запланированный поиск') ON CONFLICT DO NOTHING RETURNING id",(ident,row['employee_id'],row['target'],__import__('psycopg.types.json',fromlist=['Jsonb']).Jsonb(row['criteria']))).fetchone()
   if not result:continue
   c.execute("UPDATE search_schedules SET status='queued',job_id=%s WHERE id=%s",(ident,row['id']));added.append((row['employee_id'],ident,row['id']))
 for owner,job,schedule in added:db.audit(owner,'search.schedule_run',schedule,{'job_id':str(job)})
 return len(added)
