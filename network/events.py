"""In-process invalidations contain no company, contact or price payloads."""
from collections import deque
import threading

class EventHub:
 def __init__(self):self.lock=threading.Lock();self.sequence=0;self.events=deque(maxlen=1024);self.closed=threading.Event()
 def close(self):self.closed.set()
 def publish(self,owner,kind,role='',department='main'):
  with self.lock:
   self.sequence+=1;self.events.append({'version':self.sequence,'owner':str(owner or ''),'kind':kind,'role':role,'department':department});return self.sequence
 def changes(self,user,after):
  with self.lock:
   if self.events and after and after<self.events[0]['version']-1:return [{'version':self.sequence,'kind':'resync'}],self.sequence
   rows=[]
   for e in self.events:
    if e['version']<=after:continue
    shared=e['kind'].startswith('carrier.') or e['kind'].startswith('employee.')
    own=e['owner']==str(user['id'])
    leadership=user['role']=='director' or user['role']=='manager' and e['role']=='logist' and e['department']==user['department']
    if shared or own or leadership:rows.append({'version':e['version'],'kind':e['kind'] if not e['kind'].startswith('employee.') else 'access.changed'})
   return rows,self.sequence
