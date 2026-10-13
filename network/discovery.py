"""Two-way LAN discovery. Invitations never grant access; pinned TLS and login do."""
import ipaddress
import json
import socket
import threading
from urllib.parse import urlsplit
import requests
from zeroconf import Zeroconf,ServiceInfo,ServiceBrowser,IPVersion

HOST_TYPE='_nexol-host._tcp.local.'
EMPLOYEE_TYPE='_nexol-employee._tcp.local.'

def addresses():
    try:values={r[4][0] for r in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET)}
    except OSError:values=set()
    # macOS's computer name need not resolve in DNS; inspect actual interfaces.
    import ifaddr
    for adapter in ifaddr.get_adapters():
        for entry in adapter.ips:
            if isinstance(entry.ip,str):values.add(entry.ip)
    private=[a for a in values if ipaddress.ip_address(a).is_private and not ipaddress.ip_address(a).is_loopback]
    return private or ['127.0.0.1']
def properties(info):return {k.decode('utf-8','replace'):v.decode('utf-8','replace') if v else '' for k,v in info.properties.items()}
def is_local(address):
    try:return ipaddress.ip_address(address).is_private
    except ValueError:return False

class Listener:
    def __init__(self,callback):self.callback=callback
    def add_service(self,zc,type_,name):
        info=zc.get_service_info(type_,name,timeout=1000)
        if info:self.callback(info)
    def update_service(self,zc,type_,name):self.add_service(zc,type_,name)
    def remove_service(self,*_):pass

class HostDiscovery:
    def __init__(self,identity,port,db,interfaces=None):
        self.identity=identity;self.port=port;self.db=db
        self.zc=Zeroconf(interfaces=interfaces or addresses(),ip_version=IPVersion.V4Only)
        self.info=ServiceInfo(HOST_TYPE,'Nexol-'+identity['host_id']+'.'+HOST_TYPE,addresses=[socket.inet_aton(a) for a in addresses()],port=port,
            properties={'host_id':identity['host_id'],'fingerprint':identity['fingerprint'],'protocol':'1'},server=socket.gethostname()+'.local.')
        self.zc.register_service(self.info)
        self.browser=ServiceBrowser(self.zc,EMPLOYEE_TYPE,Listener(self.seen))
    def seen(self,info):
        data=properties(info)
        if data.get('host_id')!=self.identity['host_id']:return
        device=data.get('device_id','')[:128]
        if not device:return
        for address in info.parsed_addresses():
            if not is_local(address):continue
            with self.db.transaction(admin=True) as db:
                db.execute('''INSERT INTO devices(id,computer_name,ip) VALUES(%s,%s,%s)
                  ON CONFLICT(id) DO UPDATE SET computer_name=excluded.computer_name,ip=excluded.ip,
                    last_seen=CASE WHEN devices.employee_id IS NULL THEN now() ELSE devices.last_seen END''',(device,data.get('computer','')[:128],address))
            try:
                requests.post(f'http://{address}:{info.port}/nexol/connect',json={**self.identity,'port':self.port},timeout=2,allow_redirects=False)
            except requests.RequestException:pass
    def close(self):
        self.browser.cancel();self.zc.unregister_service(self.info);self.zc.close()

class EmployeeDiscovery:
    def __init__(self,trust,device,computer,callback_port,on_host,interfaces=None):
        self.trust=trust;self.on_host=on_host
        self.zc=Zeroconf(interfaces=interfaces or addresses(),ip_version=IPVersion.V4Only)
        self.info=ServiceInfo(EMPLOYEE_TYPE,'Nexol-'+device+'.'+EMPLOYEE_TYPE,addresses=[socket.inet_aton(a) for a in addresses()],port=callback_port,
            properties={'host_id':trust['host_id'],'device_id':device,'computer':computer,'protocol':'1'},server=socket.gethostname()+'.local.')
        self.zc.register_service(self.info)
        self.browser=ServiceBrowser(self.zc,HOST_TYPE,Listener(self.seen))
    def seen(self,info):
        data=properties(info)
        if data.get('host_id')!=self.trust['host_id'] or data.get('fingerprint')!=self.trust['fingerprint']:return
        for address in info.parsed_addresses():
            if is_local(address):self.on_host(f'https://{address}:{info.port}')
    def close(self):
        self.browser.cancel();self.zc.unregister_service(self.info);self.zc.close()
