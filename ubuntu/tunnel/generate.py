"""Generate role-separated relay bundles. All private material stays outside source."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
from uuid import UUID
import argparse,json,secrets,sys,ipaddress,shutil
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from network.internet_access import public_url

def private_key():return ec.generate_private_key(ec.SECP256R1())
def key_bytes(key):return key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
def write(path,data,private=False):
    path.write_bytes(data if isinstance(data,bytes) else data.encode('utf-8'))
    if private:path.chmod(0o600)

def generate(domain,trust,output,control_port=7000,public_port=443,host_port=8743):
    base=Path(output).resolve();source=ROOT.resolve()
    if base==source or base.is_relative_to(source):raise ValueError('Private deployment material must be generated outside the source repository')
    if base.exists():raise ValueError('Output directory already exists; do not overwrite active tunnel credentials')
    UUID(trust['host_id'])
    if not isinstance(trust.get('fingerprint'),str) or len(trust['fingerprint'])!=64 or any(x not in '0123456789abcdef' for x in trust['fingerprint']):raise ValueError('Invalid host fingerprint')
    url=public_url('https://'+domain);host=__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(url).hostname
    if __import__('urllib.parse',fromlist=['urlsplit']).urlsplit(url).port:raise ValueError('Pass the public port separately, not inside the domain')
    if any(type(p) is not int or not 1<=p<=65535 for p in (control_port,public_port,host_port)) or control_port==public_port:raise ValueError('Choose distinct valid control and public ports')
    base.mkdir(parents=True,mode=0o700);folders={r:base/r for r in ('gateway','office','employees','operator')}
    for folder in folders.values():folder.mkdir(mode=0o700)
    now=datetime.now(timezone.utc);ca_key=private_key();ca_name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'NexSearch tunnel CA '+trust['host_id'])])
    ca=x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=3650)).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True).add_extension(x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=False,data_encipherment=False,key_agreement=False,key_cert_sign=True,crl_sign=True,encipher_only=False,decipher_only=False),critical=True).sign(ca_key,hashes.SHA256())
    write(folders['operator']/'ca-private.pem',key_bytes(ca_key),True)
    token=secrets.token_urlsafe(48)
    for role in ('gateway','office'):
        folder=folders[role];key=private_key();purpose=ExtendedKeyUsageOID.SERVER_AUTH if role=='gateway' else ExtendedKeyUsageOID.CLIENT_AUTH
        name=host if role=='gateway' else 'office-'+trust['host_id']
        try:server_san=x509.IPAddress(ipaddress.ip_address(host))
        except ValueError:server_san=x509.DNSName(host)
        san=server_san if role=='gateway' else x509.UniformResourceIdentifier('urn:nexsearch:office:'+trust['host_id'])
        cert=x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,name)])).issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=825)).add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True).add_extension(x509.ExtendedKeyUsage([purpose]),critical=False).add_extension(x509.SubjectAlternativeName([san]),critical=False).sign(ca_key,hashes.SHA256())
        write(folder/'tunnel.key',key_bytes(key),True);write(folder/'tunnel.crt',cert.public_bytes(serialization.Encoding.PEM));write(folder/'ca.crt',ca.public_bytes(serialization.Encoding.PEM));write(folder/'control.token',token+'\n',True)
    common='auth.method = "token"\nauth.additionalScopes = ["HeartBeats", "NewWorkConns"]\nauth.tokenSource.type = "file"\nauth.tokenSource.file.path = "/etc/nexsearch-tunnel/control.token"\nlog.to = "console"\nlog.level = "info"\n'
    tls='transport.tls.certFile = "/etc/nexsearch-tunnel/tunnel.crt"\ntransport.tls.keyFile = "/etc/nexsearch-tunnel/tunnel.key"\ntransport.tls.trustedCaFile = "/etc/nexsearch-tunnel/ca.crt"\n'
    gateway=f'bindAddr = "0.0.0.0"\nbindPort = {control_port}\nproxyBindAddr = "0.0.0.0"\nallowPorts = [{{ single = {public_port} }}]\nmaxPortsPerClient = 1\ntransport.tls.force = true\n'+common+tls
    office=f'serverAddr = {json.dumps(host)}\nserverPort = {control_port}\nloginFailExit = false\ntransport.tls.enable = true\ntransport.tls.serverName = {json.dumps(host)}\ntransport.protocol = "tcp"\ntransport.heartbeatInterval = 15\ntransport.heartbeatTimeout = 45\n'+common+tls+f'\n[[proxies]]\nname = "nexsearch-{trust["host_id"]}"\ntype = "tcp"\nlocalIP = "127.0.0.1"\nlocalPort = {host_port}\nremotePort = {public_port}\n'
    write(folders['gateway']/'frps.toml',gateway,True);write(folders['office']/'frpc.toml',office,True)
    for role in ('gateway','office'):
        for name in ('install.py','frp-release.json','check.py'):shutil.copy2(Path(__file__).with_name(name),folders[role]/name)
    profile={'version':1,'host_id':trust['host_id'],'fingerprint':trust['fingerprint'],'url':url+(f':{public_port}' if public_port!=443 else '')}
    write(folders['employees']/'internet-access.json',json.dumps(profile,ensure_ascii=False,indent=2)+'\n')
    write(base/'deployment.json',json.dumps({'domain':host,'control_port':control_port,'public_port':public_port,'office_port':host_port,'host_id':trust['host_id'],'certificate_expires':(now+timedelta(days=825)).date().isoformat()},indent=2))
    return {'output':str(base),'endpoint':profile['url'],'host_id':trust['host_id'],'private_files_in_employee_bundle':False}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--domain',required=True);p.add_argument('--trust',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--control-port',type=int,default=7000);p.add_argument('--public-port',type=int,default=443);a=p.parse_args()
    print(json.dumps(generate(a.domain,json.loads(a.trust.read_text(encoding='utf-8')),a.output,a.control_port,a.public_port)))
