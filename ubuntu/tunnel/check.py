"""Unauthenticated end-to-end check, with certificate pin before any HTTP data."""
from pathlib import Path
from urllib.parse import urlsplit
import argparse,hashlib,http.client,json,ssl

def check(profile,timeout=5):
    parsed=urlsplit(profile['url'])
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:raise ValueError('Invalid endpoint')
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);context.check_hostname=False;context.verify_mode=ssl.CERT_NONE
    connection=http.client.HTTPSConnection(parsed.hostname,parsed.port or 443,context=context,timeout=timeout)
    try:
        connection.connect()
        if hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()!=profile['fingerprint']:raise ValueError('Certificate fingerprint mismatch; no HTTP request sent')
        connection.request('GET','/api/host-info');response=connection.getresponse();data=response.read(65537)
        if response.status!=200 or len(data)>65536:raise ValueError('Unexpected host response')
        info=json.loads(data)
        if info.get('host_id')!=profile['host_id']:raise ValueError('Host identity mismatch')
        return {'reachable':True,'pinned_certificate_verified':True,'host_id':info['host_id'],'version':info.get('version')}
    finally:connection.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--profile',type=Path,required=True);a=p.parse_args();print(json.dumps(check(json.loads(a.profile.read_text(encoding='utf-8')))))
