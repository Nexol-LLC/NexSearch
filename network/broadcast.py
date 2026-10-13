"""LAN fallback discovery. The certificate pin is checked before any credentials."""
import socket,json,threading,time
PORT=8742
class HostBroadcast:
 def __init__(self,trust,port):
  self.closed=threading.Event();self.sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);self.sock.bind(('',PORT));self.sock.settimeout(1)
  self.trust=trust;self.port=port;self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
 def run(self):
  while not self.closed.is_set():
   try:
    data,peer=self.sock.recvfrom(2048);query=json.loads(data)
    if query.get('protocol')!='nexsearch-discovery-1':continue
    reply={'protocol':'nexsearch-discovery-1','nonce':query.get('nonce'),'host_id':self.trust['host_id'],'port':self.port,'fingerprint':self.trust['fingerprint']}
    self.sock.sendto(json.dumps(reply).encode(),peer)
   except (OSError,ValueError,AttributeError):pass
 def close(self):self.closed.set();self.sock.close();self.thread.join(timeout=2)
class EmployeeBroadcast:
 def __init__(self,trust,found):
  self.closed=threading.Event();self.trust=trust;self.found=found;self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
 def run(self):
  from uuid import uuid4
  from .discovery import is_local
  while not self.closed.is_set():
   sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
   try:
    sock.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1);sock.settimeout(1);nonce=str(uuid4());query=json.dumps({'protocol':'nexsearch-discovery-1','nonce':nonce}).encode()
    sock.sendto(query,('255.255.255.255',PORT));until=time.monotonic()+3
    while time.monotonic()<until and not self.closed.is_set():
     try:data,peer=sock.recvfrom(2048);info=json.loads(data)
     except (OSError,ValueError):continue
     if info.get('nonce')==nonce and info.get('host_id')==self.trust['host_id'] and info.get('fingerprint')==self.trust['fingerprint'] and is_local(peer[0]) and info.get('port')==8743:self.found('https://'+peer[0]+':8743')
   except OSError:pass
   finally:sock.close()
   self.closed.wait(12)
 def close(self):self.closed.set();self.thread.join(timeout=4)
